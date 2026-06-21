import atexit
import builtins
import contextlib
import hashlib
import json
import logging
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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

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

QWEN_ASR_IMPORT_ERROR: Exception | None = None
try:
    from qwen_asr import Qwen3ASRModel
except Exception as exc:
    Qwen3ASRModel = None
    QWEN_ASR_IMPORT_ERROR = exc

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
from evelyn_core.autonomy_observation_state import (
    build_autonomy_recent_context_payload,
    build_autonomy_status_payload,
    build_autonomy_summary_payload,
    build_default_autonomy_observation,
    pick_recent_user_text,
)
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
from evelyn_core.minecraft_assets import MinecraftItemIconLoader
from evelyn_core.memory_vault import (
    ensure_memory_vault_layout,
    export_memory_graph,
    memory_vault_user_note,
    memory_vault_user_snapshot,
    update_memory_vault_user_note,
)
from evelyn_core.json_safety import safe_json_dumps, safe_json_value
from evelyn_core.minecraft_runtime_snapshot import (
    attach_minecraft_runtime_snapshot,
    extract_minecraft_recent_activity_live,
    format_minecraft_state_summary,
    format_position_short,
    normalize_inventory_slot_entries,
    normalize_inventory_top_entries,
    normalize_inventory_used_slots,
    normalize_minecraft_item_name,
    merge_voyager_status_into_state,
    summarize_inventory_top,
)
from evelyn_core.question_shaping import (
    enforce_question_limits,
    filter_stream_chunk_for_question_limits,
)
from evelyn_core.proactive_questions import (
    evaluate_proactive_question_gate,
    select_question_to_ask,
)
from evelyn_core.cognitive_policy_state import (
    apply_ask_gating,
    ask_confidence_threshold_for_source,
    build_cognitive_fallback_state,
    build_fast_cognitive_state,
    finalize_cognitive_state,
    policy_response_for_state,
    read_cached_cognitive_state,
    read_layered_cognitive_state,
)
from evelyn_core.self_model import (
    mark_self_state_assistant_output,
    record_self_identity_turn,
    render_self_judgment_context,
    render_self_state_context,
    update_self_state_for_turn,
)
from evelyn_core.vision_watch import (
    capture_vision_watch_frame,
    read_vision_watch_state,
    render_vision_watch_context,
    update_vision_watch_analysis,
    vision_watch_scene_is_unreliable,
)
from evelyn_core.vision_quality import build_vision_quality
from evelyn_core.text import (
    apply_stt_post_corrections,
    clean_text,
    clean_tts_text,
    contains_leading_wake_word,
    contains_wake_word,
    extract_leading_wake_alias,
    fuzzy_leading_wake_alias,
    is_user_echo_answer,
    is_similar,
    looks_like_brief_filler_text,
    looks_like_gibberish_probe,
    looks_like_repetitive_noise_text,
    normalize_omnivoice_tags,
    normalize_voice_text,
    normalized_wake_words,
    strip_leading_voice_fillers,
    strip_model_channel_tags,
    strip_omnivoice_tags,
    strip_response_action_tags,
    strip_voice_wake_word,
    visible_text,
)
from evelyn_core.session_memory_state import (
    SessionStateStore,
    build_topic_id as build_session_topic_id,
    is_casual_call_or_status_question as session_is_casual_call_or_status_question,
    new_conversation_history as new_session_conversation_history,
    new_turn_id as new_session_turn_id,
    runtime_session_key as resolve_runtime_session_key,
)
from evelyn_core.room_speaker_activity import RoomSpeakerActivityStore
from evelyn_core.response_output_policy import (
    answer_contains_minecraft_leak as answer_contains_minecraft_leak_payload,
    answer_simple_local_chat_query as answer_simple_local_chat_query_payload,
    cleanup_assistant_display_artifacts as cleanup_assistant_display_artifacts_payload,
    extract_answer_from_reasoning as extract_answer_from_reasoning_payload,
    fallback_for_unrequested_minecraft_leak as fallback_for_unrequested_minecraft_leak_payload,
    format_display_text as format_display_text_payload,
    normalize_friend_style_output,
    parse_response_action_tag,
    sanitize_model_output as sanitize_model_output_payload,
    sanitize_unrequested_minecraft_leak as sanitize_unrequested_minecraft_leak_payload,
    user_explicitly_mentions_minecraft as user_explicitly_mentions_minecraft_payload,
)
from evelyn_core.search_followup_policy import (
    answer_promises_search,
    strip_search_answer_sources,
)
from evelyn_core.search_query_context import build_search_query_from_context
from evelyn_core.search_tools import search_duckduckgo as search_duckduckgo_payload
from evelyn_core.runtime_status_context import (
    answer_gpu_runtime_status_query,
    compact_runtime_error,
    load_runtime_gpu_status,
    load_runtime_recent_errors,
    probe_runtime_tcp_service,
    runtime_status_port_from_url,
)
from evelyn_core.route_fallback_policy import (
    classify_llm_route_fallback,
    normalize_route_name,
    should_force_voice_context_route,
)
from evelyn_core.tool_awareness_policy import build_tool_awareness_context
from evelyn_core.local_tool_diagnostic_context import build_local_tool_diagnostic_context
from evelyn_core.llm_context_assembly import LlmContextAssemblyDeps, prepare_llm_messages_from_runtime
from evelyn_core.memory_context_state import build_memory_context
from evelyn_core.memory_layers import collect_memory_layers
from evelyn_core.memory_llm_context import (
    build_cognitive_state_messages,
    build_compact_cognitive_state_messages,
    layered_summary_text,
    recent_memory_groups,
)
from evelyn_core.memory_update_policy import (
    build_memory_writer_decision_for_turn,
    build_memory_writer_decision_payload,
    memory_refresh_inputs_for_turn,
    plan_memory_writebehind_schedule,
    redact_vision_text_for_memory as redact_vision_text_for_memory_payload,
    write_memory_turn_records,
)
from evelyn_core.memory_writeback_state import (
    run_long_term_memory_update,
)
from control_page_runtime_health import (
    build_control_page_runtime_health,
    is_control_api_ready_from_runtime_services,
)
from runtime_lifecycle import (
    launch_runtime_restart_sequence,
    schedule_evelyn_local_shutdown as runtime_schedule_evelyn_local_shutdown,
    schedule_evelyn_stack_shutdown as runtime_schedule_evelyn_stack_shutdown,
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
    build_tool_use_decisions,
    build_vision_context_hint,
    render_tool_use_context,
)
from evelyn_core.discord_delivery import (
    DiscordStreamingVoiceDeliveryRequest,
    build_streaming_voice_delivery,
    execute_streaming_voice_delivery_plan,
    send_discord_text,
)
from evelyn_core.discord_commands import (
    build_autonomy_status_command_text,
    build_channel_setting_list_reply,
    build_command_channel_usage,
    build_help_command_text,
    build_minecraft_connect_reply,
    build_minecraft_goal_missing_reply,
    build_minecraft_goal_updated_reply,
    build_minecraft_status_command_text,
    build_observe_channel_usage,
    build_prefix_current_reply,
    build_prefix_reset_reply,
    build_prefix_saved_reply,
    build_reset_guild_memory_reply,
    build_status_command_text,
    control_command_check_failure_message,
    guild_only_command_message,
    is_control_command_authorized_payload,
    normalize_channel_setting_action,
)
from evelyn_core.discord_command_handlers import (
    handle_autonomy_start_command,
    handle_autonomy_status_command,
    handle_autonomy_stop_command,
    handle_channel_setting_command,
    handle_evelyn_page_command,
    handle_join_voice_command,
    handle_leave_voice_command,
    handle_minecraft_connect_command,
    handle_minecraft_disconnect_command,
    handle_minecraft_goal_command,
    handle_minecraft_status_command,
    handle_prefix_command,
    handle_rejoin_voice_command,
    handle_reset_guild_memory_command,
    handle_restart_bot_command,
    handle_shutdown_bot_command,
    handle_status_command,
)
from evelyn_core.discord_settings import (
    add_guild_channel_setting as add_guild_channel_setting_payload,
    get_guild_command_only_channel_ids as get_guild_command_only_channel_ids_payload,
    get_guild_command_prefix as get_guild_command_prefix_payload,
    get_guild_observe_channel_ids as get_guild_observe_channel_ids_payload,
    normalize_command_prefix as normalize_command_prefix_payload,
    remove_guild_channel_setting as remove_guild_channel_setting_payload,
    save_guild_channel_list as save_guild_channel_list_payload,
    save_guild_command_prefix as save_guild_command_prefix_payload,
)
from evelyn_core.discord_ingress import (
    build_voice_ingress_context,
    resolve_text_thread_id,
    make_person_memory_key as make_discord_person_memory_key,
    make_room_memory_key as make_discord_room_memory_key,
    make_session_memory_key as make_discord_session_memory_key,
    make_text_reply_slot_key as make_discord_text_reply_slot_key,
    make_text_session_key as make_discord_text_session_key,
    make_voice_room_session_key as make_discord_voice_room_session_key,
    make_voice_session_key as make_discord_voice_session_key,
    normalize_voice_debug_meta,
    voice_ingress_source,
)
from evelyn_core.discord_text_turn import DiscordTextMessageHandlerDeps, handle_discord_text_message
from evelyn_core.discord_session_policy import (
    DiscordRoomSessionPolicy,
    LocalMicDiscordSuppressionInput,
    TtsInterruptMeta,
    VoiceReplyGateInput,
    decide_local_mic_discord_suppression,
    decide_voice_reply_gate,
    is_short_followup_candidate_policy,
    should_ignore_short_transcription_policy,
    should_interrupt_tts,
    should_skip_full_stt_after_wake_probe_policy,
)
from evelyn_core.skills import skill_registry
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
from evelyn_core.local_mic_state import (
    build_local_mic_runtime_state,
    local_mic_status_line_from_payload,
    normalize_voice_input_mode,
    serialize_local_mic_runtime_state_payload,
    set_voice_input_mode_state,
    voice_input_mode_status_line_from_mode,
)
from evelyn_core.local_tts_playback import LocalTtsPlaybackManager
from evelyn_core.observability_metrics import (
    ModelCallMetricsStore,
    summarize_voice_p95_metrics,
)
from evelyn_core.page_urls import resolve_public_page_url
from evelyn_core.query_intents import (
    answer_current_datetime_query,
    should_force_search_query,
)
from evelyn_core.question_policy_state import (
    QuestionPolicyState,
    default_question_metrics,
    extract_question_policy_from_route_meta as extract_question_policy_from_route_meta_payload,
    is_continuable_technical_topic as is_continuable_technical_topic_payload,
    normalize_question_policy_mapping as normalize_question_policy_mapping_payload,
    user_frustration_with_questions as user_frustration_with_questions_payload,
    user_wants_direct_answer as user_wants_direct_answer_payload,
)
from evelyn_core.assistant_contracts import (
    TtsSynthRequest,
    TtsSynthResult,
)
from evelyn_core.assistant_prompt_contract import build_evelyn_system_prompt
from evelyn_core.control_page_contracts import memory_panel_reply
from evelyn_core.control_page_http import (
    add_control_page_no_store_headers,
    build_control_page_health_payload,
    control_page_cors_middleware,
    control_page_file_response,
    control_page_json_response,
    resolve_control_page_asset_path,
)
from evelyn_core.control_page_state import (
    ControlPageChatLogStore,
    ControlPageMinecraftSnapshotCache,
    ControlPageRuntimeServicesCache,
    ControlPageUiCommandStore,
    build_control_page_autonomy_reply_payload,
    build_control_page_boot_progress_payload,
    build_control_page_inventory_reply_payload,
    build_control_page_local_status_text_payload,
    build_control_page_minecraft_reply_payload,
    build_control_page_runtime_services_error_payload,
    build_control_page_status_text_payload,
    build_control_page_voice_continuity_reply_payload,
    build_control_page_voice_status_reply_payload,
    command_status,
    control_page_open_memory_vault_result,
    control_page_open_memory_vault_tool_reply,
    control_page_result_status,
    execute_control_page_memory_tool,
    execute_control_page_minecraft_tool,
    execute_control_page_runtime_tool,
    execute_control_page_voice_tool,
    handle_control_page_chat_request,
    handle_control_page_memory_note_action_request,
    handle_control_page_shutdown_request,
    memory_vault_obsidian_url,
    parse_control_page_guild_id,
    parse_control_page_memory_graph_query,
    parse_control_page_memory_note_query,
    parse_control_page_memory_snapshot_query,
    sanitize_control_page_welcome_text_payload,
)
from evelyn_core.control_page_state_handler import ControlPageStateDeps, build_control_page_state_from_runtime
from evelyn_core.control_page_runtime_probe import probe_control_page_runtime_services
from evelyn_core.control_page_tools import (
    CONTROL_PAGE_COMMANDS,
    build_control_page_all_commands,
    build_control_page_commands,
    build_control_page_help_reply,
    cheap_control_page_tool_decision,
    control_page_tool_decision,
    control_page_tool_decision_from_llm,
    control_page_tool_policy_error,
    control_page_tool_reply_from_execution,
    control_page_tool_registry_prompt,
    control_page_ui_tool_action_from_decision,
    is_control_page_question_text,
    is_explicit_control_page_restart_request,
    should_route_control_page_tool_candidate,
)
from evelyn_core.tts_playback import (
    CachedWaveAudioSource,
    ChunkWindow,
    OmniVoicePCMStream,
    StreamingVoiceDelivery,
    SpeechChunker,
    TTSQueueSink,
    TtsPlaybackManager,
    TtsSourcePlaybackRequest,
    TtsStreamingPlaybackRequest,
    TtsPlaybackTracker,
    add_omnivoice_stream_contract,
    clear_tts_playback_tracking,
    configure_tts_playback_logging,
    get_tracked_tts_playback,
    is_tracked_tts_playback_active,
    prefetch_tts_sources,
    resolve_cached_tts_audio_path,
    split_tts_sentences,
    tracked_tts_playback_count,
    tracked_tts_playback_guild_ids,
)
from evelyn_core.turn_trace import TURN_SUMMARY_EVENTS, build_turn_summary_payload, write_turn_trace_event
from evelyn_core.turn_lifecycle import TurnScope, TurnScopeRegistry, TurnState
from evelyn_core.turn_budget import build_turn_execution_budget
from evelyn_core.voice_stt_flow import (
    apply_fuzzy_wake_near_miss,
    apply_strict_wake_confirm_policy,
    build_final_transcript_flow,
    decide_final_wake_veto,
    interpret_wake_probe_result,
    run_full_stt_with_optional_rescore,
    run_partial_stt_flow,
)
from evelyn_core.stt_client import transcribe_audio16k_via_service
from evelyn_core.speaker_verification import (
    SpeakerVerificationConfig,
    SpeakerVerificationResult,
    SpeakerVerifier,
    speaker_verification_applies,
)
from evelyn_core.voice_barge_in import (
    VoiceUtteranceMergeRecord,
    maybe_merge_barge_in_utterance,
)
from evelyn_core.voice_barge_in_continuity import (
    VOICE_BARGE_IN_EVENT_FINISH,
    VOICE_BARGE_IN_REASON_CODE,
    VOICE_BARGE_IN_REASON_LABEL,
    VoiceBargeInContinuityTracker,
)
from evelyn_core.voice_debug_audio import (
    build_voice_debug_audio_item,
    save_voice_debug_audio_now as save_voice_debug_audio_now_payload,
    voice_debug_drop_message,
)
from evelyn_core.voice_utterance import (
    UtteranceAssemblyConfig,
    discord_pcm_seconds,
    merge_debug_meta,
    merge_discord_pcm_segments,
)
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
    enqueue_voice_ingress_item,
    evaluate_voice_ingress_dequeue,
    process_voice_reply_from_transcript_context,
)
from evelyn_core.voice_route_execution import (
    VoiceMainLlmStreamingDeps,
    VoiceRouteExecutionDeps,
    execute_main_llm_streaming_turn as execute_main_llm_streaming_turn_with_deps,
    execute_search_then_answer_action as execute_search_then_answer_action_with_deps,
    maybe_execute_registered_route as maybe_execute_registered_route_with_deps,
    maybe_handle_short_circuit_route as maybe_handle_short_circuit_route_with_deps,
    prepare_route_context as prepare_route_context_with_deps,
)
from evelyn_core.voice_pipeline import (
    ActionResult,
    AnswerPayload,
    DeliveryPlan,
    RouteDecision,
    TranscriptResult,
    VoiceReplyRequest,
    VoiceSegment,
    build_answer_payload,
    build_answer_payload_from_text,
    build_delivery_plan,
    build_route_decision,
    build_transcript_result,
    build_voice_reply_request,
    build_voice_segment,
    classify_dialogue_turn,
)
from evelyn_core.voice_pipeline_state import (
    build_voice_pipeline_snapshot_payload,
    default_voice_pipeline_counters,
    default_voice_pipeline_state,
    increment_voice_counter,
    load_last_voice_channel_state as load_last_voice_channel_state_payload,
    mark_last_voice_manual_disconnect,
    record_voice_failure_state,
    save_last_voice_channel_state as save_last_voice_channel_state_payload,
    voice_last_channel_state_path as resolve_voice_last_channel_state_path,
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
VOICE_UTTERANCE_ASSEMBLY_ENABLED = os.getenv("VOICE_UTTERANCE_ASSEMBLY_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
VOICE_UTTERANCE_COMMIT_WAIT_SEC = float(os.getenv("VOICE_UTTERANCE_COMMIT_WAIT_SEC", "0.22"))
VOICE_UTTERANCE_PAD_MS = int(os.getenv("VOICE_UTTERANCE_PAD_MS", "180"))
VOICE_UTTERANCE_MAX_AUDIO_SEC = float(os.getenv("VOICE_UTTERANCE_MAX_AUDIO_SEC", "14.0"))
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
DISCORD_ENABLED = os.getenv("DISCORD_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
LOCAL_ONLY_MODE = os.getenv("LOCAL_ONLY", "false").lower() in {"1", "true", "yes", "on"} or not DISCORD_ENABLED
LOCAL_TTS_OUTPUT_ENABLED = os.getenv(
    "LOCAL_TTS_OUTPUT_ENABLED",
    "true" if LOCAL_ONLY_MODE else "false",
).lower() in {"1", "true", "yes", "on"}
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
CONTROL_PAGE_WELCOME_LLM_TIMEOUT_SEC = float(os.getenv("CONTROL_PAGE_WELCOME_LLM_TIMEOUT_SEC", "18.0"))
CONTROL_PAGE_WELCOME_FALLBACK = os.getenv(
    "CONTROL_PAGE_WELCOME_FALLBACK",
    "왔어? 오늘도 이상한 건 내가 정리하고, 재밌는 건 같이 키워볼게.",
)
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
control_page_minecraft_item_icon_loader = MinecraftItemIconLoader(PROJECT_ROOT)
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
    "model_call",
    "question_trace",
    "text_turn_summary",
    "voice_turn_summary",
    "voice_drop_summary",
    "barge_in_continuity",
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
room_last_voice_utterance_for_merge: dict[str, VoiceUtteranceMergeRecord] = {}
session_partial_stt_text: dict[str, str] = {}
session_committed_stt_text: dict[str, str] = {}
session_bad_audio_counts: dict[str, int] = {}
session_state_store = SessionStateStore(
    histories=session_histories,
    followup_targets=session_followup_targets,
    active_until=active_session_until,
    active_user_ids=active_session_user_ids,
    last_active_at=session_last_active_at,
    awaiting_user_reply=session_awaiting_user_reply,
    last_speaker=session_last_speaker,
    topic_ids=session_topic_ids,
    turn_ids=session_turn_ids,
    segment_counters=session_segment_counters,
    last_turn_accepted_at=session_last_turn_accepted_at,
    last_stt_text=session_last_stt_text,
    partial_stt_text=session_partial_stt_text,
    committed_stt_text=session_committed_stt_text,
    bad_audio_counts=session_bad_audio_counts,
)
room_owner_user_ids: dict[str, int] = {}
room_owner_until: dict[str, float] = {}
room_reply_in_progress: dict[str, bool] = {}
voice_connect_locks: dict[int, asyncio.Lock] = {}
instance_lock_handle = None
instance_lock_path = Path(os.getenv("EVELYN_INSTANCE_LOCK_PATH", str(Path(__file__).resolve().with_name(".evelyn_bot.lock"))))


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
    return normalize_command_prefix_payload(prefix, default_prefix=DEFAULT_COMMAND_PREFIX)


def get_guild_command_prefix(guild_id: int | None) -> str:
    return get_guild_command_prefix_payload(
        guild_id,
        prefix_cache=guild_prefix_cache,
        default_prefix=DEFAULT_COMMAND_PREFIX,
    )


def save_guild_command_prefix(guild_id: int, prefix: str) -> str:
    return save_guild_command_prefix_payload(
        guild_id,
        prefix,
        prefix_cache=guild_prefix_cache,
        default_prefix=DEFAULT_COMMAND_PREFIX,
    )


def get_guild_observe_channel_ids(guild_id: int | None) -> list[int]:
    return get_guild_observe_channel_ids_payload(guild_id)


def get_guild_command_only_channel_ids(guild_id: int | None) -> list[int]:
    return get_guild_command_only_channel_ids_payload(guild_id)


def save_guild_channel_list(guild_id: int, key: str, channel_ids: list[int]) -> list[int]:
    return save_guild_channel_list_payload(guild_id, key, channel_ids)


def add_guild_channel_setting(guild_id: int, key: str, channel_id: int) -> list[int]:
    return add_guild_channel_setting_payload(guild_id, key, channel_id)


def remove_guild_channel_setting(guild_id: int, key: str, channel_id: int) -> list[int]:
    return remove_guild_channel_setting_payload(guild_id, key, channel_id)


async def resolve_command_prefix(_bot, message: discord.Message):
    prefix = get_guild_command_prefix(message.guild.id if message.guild else None)
    return commands.when_mentioned_or(prefix)(_bot, message)


bot = commands.Bot(command_prefix=resolve_command_prefix, intents=intents, help_command=None)

SYSTEM_PROMPT = build_evelyn_system_prompt(omnivoice_tag_guidance=OMNIVOICE_TAG_GUIDANCE)

session_locks: dict[str, asyncio.Lock] = {}
reply_slot_locks: dict[str, asyncio.Lock] = {}
tts_lock = asyncio.Lock()
tts_playback_tracker = TtsPlaybackTracker()
tts_playback_manager = TtsPlaybackManager(tts_playback_tracker)
local_tts_playback_manager = LocalTtsPlaybackManager(
    enabled=LOCAL_TTS_OUTPUT_ENABLED,
    device=LOCAL_TTS_OUTPUT_DEVICE,
    log=print,
)
speaker_verifier = SpeakerVerifier(
    SpeakerVerificationConfig(
        enabled=SPEAKER_VERIFICATION_ENABLED,
        enroll_dir=SPEAKER_VERIFICATION_ENROLL_DIR,
        threshold=SPEAKER_VERIFICATION_THRESHOLD,
        min_audio_sec=SPEAKER_VERIFICATION_MIN_AUDIO_SEC,
        max_audio_sec=SPEAKER_VERIFICATION_MAX_AUDIO_SEC,
        model=SPEAKER_VERIFICATION_MODEL,
        cache_dir=SPEAKER_VERIFICATION_CACHE_DIR,
        device=SPEAKER_VERIFICATION_DEVICE,
    ),
    log=print,
)
active_tts_playbacks = tts_playback_tracker.registry
voice_debug_counts: dict[int, int] = {}
voice_debug_stems: dict[tuple[int, str, str, str], str] = {}

tts_warmup_started = False
stt_processor: Optional[Any] = None
stt_model: Optional[Any] = None
stt_backend: Optional[str] = None
http_session: Optional[aiohttp.ClientSession] = None
startup_components_ready = False
startup_components_task: Optional[asyncio.Task] = None
startup_component_state: dict[str, dict[str, Any]] = {}
vision_watch_task: Optional[asyncio.Task] = None
voice_path_warmup_locks: dict[str, asyncio.Lock] = {}
voice_path_warmup_done: dict[str, float] = {}
partial_stt_cache: dict[str, dict[str, Any]] = {}
voice_utterance_assembly_config = UtteranceAssemblyConfig(
    enabled=VOICE_UTTERANCE_ASSEMBLY_ENABLED,
    commit_wait_sec=VOICE_UTTERANCE_COMMIT_WAIT_SEC,
    pad_ms=VOICE_UTTERANCE_PAD_MS,
    max_audio_sec=VOICE_UTTERANCE_MAX_AUDIO_SEC,
)

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
voice_utterance_buffers: dict[str, dict[str, Any]] = {}
voice_utterance_flush_tasks: dict[str, asyncio.Task] = {}
debug_write_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max(8, DEBUG_WRITE_QUEUE_MAX))
debug_write_task: asyncio.Task | None = None


local_mic_service: LocalMicCaptureService | None = None
local_mic_runtime_state: dict[str, Any] = build_local_mic_runtime_state(
    enabled=LOCAL_MIC_ENABLED,
    input_mode=VOICE_INPUT_MODE,
    routed_user_ids=LOCAL_MIC_DISCORD_USER_IDS,
)


@dataclass(slots=True)
class LocalControlVoiceGuild:
    id: int = LOCAL_CONTROL_GUILD_ID
    name: str = LOCAL_CONTROL_GUILD_NAME
    voice_client: Any = None


@dataclass(slots=True)
class LocalControlVoiceClient:
    guild: LocalControlVoiceGuild
    local_speaker_output: bool = True
    channel: Any = None


@dataclass(slots=True)
class LocalControlVoiceMember:
    id: int
    display_name: str
    name: str
    guild: LocalControlVoiceGuild
    bot: bool = False


def local_control_voice_member() -> LocalControlVoiceMember:
    user_id = min(LOCAL_MIC_DISCORD_USER_IDS) if LOCAL_MIC_DISCORD_USER_IDS else LOCAL_CONTROL_GUILD_ID
    guild = LocalControlVoiceGuild()
    guild.voice_client = LocalControlVoiceClient(guild=guild)
    return LocalControlVoiceMember(
        id=int(user_id),
        display_name=os.getenv("LOCAL_MIC_USER_NAME", "정훈"),
        name=os.getenv("LOCAL_MIC_USER_NAME", "정훈"),
        guild=guild,
    )


def is_local_speaker_voice_client(vc: Any) -> bool:
    return bool(getattr(vc, "local_speaker_output", False))


control_page_runner: web.AppRunner | None = None
control_page_site: web.TCPSite | None = None
control_page_start_lock: asyncio.Lock | None = None
control_page_chat_log_store = ControlPageChatLogStore(limit=CONTROL_PAGE_CHAT_LOG_LIMIT)
control_page_minecraft_snapshot_cache = ControlPageMinecraftSnapshotCache(
    stale_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC,
    expired_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC,
)
control_page_minecraft_snapshot_lock: asyncio.Lock | None = None
control_page_minecraft_snapshot_refresh_task: asyncio.Task | None = None
control_page_minecraft_snapshot_poll_task: asyncio.Task | None = None
control_page_runtime_services_cache = ControlPageRuntimeServicesCache(
    stale_after_sec=CONTROL_PAGE_RUNTIME_CACHE_REFRESH_SEC,
    expired_after_sec=CONTROL_PAGE_RUNTIME_CACHE_MAX_STALE_SEC,
    refresh_min_interval_sec=CONTROL_PAGE_RUNTIME_CACHE_REFRESH_MIN_INTERVAL_SEC,
)
control_page_runtime_services_lock: asyncio.Lock | None = None
control_page_runtime_services_refresh_task: asyncio.Task | None = None
control_page_ui_command_store = ControlPageUiCommandStore(limit=40)
control_page_welcome_locks: dict[int, asyncio.Lock] = {}
runtime_status_context_cache: dict[str, Any] = {"text": "", "cached_at": 0.0}
runtime_status_context_lock: asyncio.Lock | None = None
room_recent_speaker_stats: dict[str, dict[int, dict[str, float]]] = {}
room_speaker_activity_store = RoomSpeakerActivityStore(
    recent_speaker_stats=room_recent_speaker_stats,
    room_owner_user_ids=room_owner_user_ids,
    room_owner_until=room_owner_until,
)
session_speculative_policies: dict[str, dict[str, Any]] = {}
room_turn_scopes: dict[str, "TurnScope"] = {}
turn_scope_registry = TurnScopeRegistry(room_turn_scopes=room_turn_scopes)
turn_stage_metrics: dict[str, dict[str, float]] = {}
turn_path_metrics: dict[str, dict[str, Any]] = {}
model_call_metrics: dict[str, dict[str, Any]] = {}
model_call_metrics_store = ModelCallMetricsStore(
    model_call_metrics=model_call_metrics,
    turn_path_metrics=turn_path_metrics,
    summary_events=TURN_SUMMARY_EVENTS,
    trace_log_dir=TURN_TRACE_LOG_DIR,
    print_fn=print,
)
question_metrics: dict[str, Any] = default_question_metrics()
session_question_state: dict[str, dict[str, Any]] = {}
question_policy_state = QuestionPolicyState(
    question_metrics=question_metrics,
    session_question_state=session_question_state,
    log_turn_event=lambda event, **payload: log_turn_event(event, **payload),
    question_feature_enabled=QUESTION_FEATURE_ENABLED,
    min_turn_gap=QUESTION_MIN_TURN_GAP,
    min_seconds_gap=QUESTION_MIN_SECONDS_GAP,
    max_per_10_turns=QUESTION_MAX_PER_10_TURNS,
    disable_after_frustration_sec=QUESTION_DISABLE_AFTER_FRUSTRATION_SEC,
)
autonomy_engines: dict[int, AutonomyEngine] = {}
last_autonomy_ping_at: dict[int, float] = {}
autonomy_last_cognitive_refresh_at: dict[int, float] = {}
autonomy_cognitive_refresh_tasks: dict[int, asyncio.Task] = {}
search_followup_queued_count = 0
inflight_llm_requests = 0
stt_inference_lock: asyncio.Lock | None = None
stt_cooldown_until = 0.0
voice_pipeline_counters: dict[str, int] = default_voice_pipeline_counters()
VOICE_BARGE_IN_CONTINUITY_TARGET = 5
voice_pipeline_state: dict[str, Any] = default_voice_pipeline_state()
recent_skill_dispatches: dict[str, float] = {}
SKILL_DISPATCH_CACHE_TTL_SEC = 300.0
SKILL_DISPATCH_REPEAT_WINDOW_SEC = 5.0
SKILL_DISPATCH_CACHE_MAX = 1024


# =========================================================
# 유틸
# =========================================================
def new_conversation_history() -> list[dict]:
    return new_session_conversation_history(SYSTEM_PROMPT)


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
            await send_discord_text(channel, text)

    def _has_queued_proactive_question(session_key: str, latest_user_text: str) -> bool:
        if question_cooldown_hit(session_key):
            return False
        gate = evaluate_proactive_question_gate(
            guild_id=guild_id,
            source="autonomy",
            user_text=latest_user_text,
            answer_text="",
            awaiting_user_reply=False,
            session_scope_key=session_key,
            session_cooldown_hit=False,
        )
        if not gate.allowed:
            return False
        for scope_type, scope_key in proactive_question_scope_candidates(session_memory_key=session_key):
            if select_question_to_ask(
                guild_id,
                scope_type=scope_type,
                scope_key=scope_key,
                session_scope_key=session_key,
            ):
                return True
        return False

    async def _default_observe() -> dict[str, Any]:
        channel = await _find_followup_channel()
        session_key = runtime_session_key(guild_id=guild_id)
        history = get_conversation_history(session_key=session_key, guild_id=guild_id)
        latest_user_text = pick_recent_user_text(history)
        observe_channel_ids = get_guild_observe_channel_ids(guild_id)
        command_only_channel_ids = get_guild_command_only_channel_ids(guild_id)
        observed_channels: list[dict[str, Any]] = []
        guild = bot.get_guild(guild_id)
        now_local = time.localtime()
        quiet_hours = now_local.tm_hour < 8 or now_local.tm_hour >= 23
        last_result = (autonomy_engines.get(guild_id).state.last_step_result if autonomy_engines.get(guild_id) is not None else {}) or {}
        cached_cognitive = read_cached_cognitive_state(guild_id)
        last_refresh_at = float(autonomy_last_cognitive_refresh_at.get(guild_id, 0.0) or 0.0)
        router_refresh_inflight = bool((task := autonomy_cognitive_refresh_tasks.get(guild_id)) is not None and not task.done())
        if guild is not None:
            for channel_id in observe_channel_ids[:8]:
                channel_obj = guild.get_channel(channel_id)
                channel_name = getattr(channel_obj, "name", str(channel_id)) if channel_obj is not None else str(channel_id)
                observed_channels.append({"id": channel_id, "name": channel_name})
        vision_watch = read_vision_watch_state()
        local_tts_state = local_tts_playback_manager.snapshot()
        local_mic_state = serialize_local_mic_runtime_state()
        queued_proactive_question_available = bool(
            session_key and _has_queued_proactive_question(session_key, latest_user_text)
        )
        return build_default_autonomy_observation(
            connected=channel is not None,
            known_followup_channels=len([v for v in session_followup_targets.values() if isinstance(v, dict) and v.get("channel_id")]),
            inflight_llm_requests=inflight_llm_requests,
            active_sessions=len(active_session_until),
            history=history,
            last_autonomy_ping_at=float(last_autonomy_ping_at.get(guild_id, 0.0) or 0.0),
            observe_channel_ids=observe_channel_ids,
            command_only_channel_ids=command_only_channel_ids,
            observed_channels=observed_channels,
            quiet_hours=quiet_hours,
            last_result=last_result,
            cached_cognitive=cached_cognitive,
            last_cognitive_refresh_at=last_refresh_at,
            router_refresh_inflight=router_refresh_inflight,
            autonomy_cognitive_stale_sec=AUTONOMY_COGNITIVE_STALE_SEC,
            autonomy_cognitive_min_interval_sec=AUTONOMY_COGNITIVE_MIN_INTERVAL_SEC,
            autonomy_cognitive_force_refresh_sec=AUTONOMY_COGNITIVE_FORCE_REFRESH_SEC,
            vision_watch=vision_watch,
            vision_watch_interval_sec=VISION_WATCH_INTERVAL_SEC,
            local_tts_state=local_tts_state,
            local_mic_state=local_mic_state,
            queued_proactive_question_available=queued_proactive_question_available,
            answer_promises_search_fn=answer_promises_search,
        )

    async def _default_send_followup(
        text: str,
        *,
        awaiting_user_reply: bool = False,
        user_text: str = "[autonomy]",
    ) -> dict[str, Any]:
        channel = await _find_followup_channel()
        if channel is None:
            return {"status": "blocked", "reason": "no_followup_channel"}
        await send_discord_text(channel, text)
        session_key = runtime_session_key(guild_id=guild_id)
        append_history(session_key, user_text or "[autonomy]", text, guild_id=guild_id)
        schedule_memory_update(
            guild_id,
            user_text or "[autonomy]",
            text,
            source="autonomy",
            assistant_speaker="Evelyn-Autonomy",
            session_key=session_key,
            runtime_mode="batch",
        )
        mark_session_active(
            session_key,
            ttl_sec=ACTIVE_CONVERSATION_TEXT_QUESTION_SEC if awaiting_user_reply else ACTIVE_CONVERSATION_TEXT_SEC,
            speaker="assistant",
            awaiting_user_reply=awaiting_user_reply,
            topic_id=build_topic_id("autonomy", text),
            answer_text=text,
            user_text=user_text or "[autonomy]",
        )
        last_autonomy_ping_at[guild_id] = time.monotonic()
        mark_self_state_assistant_output(proactive=True)
        return {"status": "ok", "reason": "sent_followup", "text": text}

    async def _default_summarize() -> dict[str, Any]:
        history = get_conversation_history(session_key=runtime_session_key(guild_id=guild_id), guild_id=guild_id)
        return build_autonomy_summary_payload(
            history,
            active_sessions=len(active_session_until),
            inflight_llm_requests=inflight_llm_requests,
        )

    async def _default_check_status() -> dict[str, Any]:
        channel = await _find_followup_channel()
        return build_autonomy_status_payload(
            connected=channel is not None,
            active_sessions=len(active_session_until),
            inflight_llm_requests=inflight_llm_requests,
            known_followup_channels=len([v for v in session_followup_targets.values() if isinstance(v, dict) and v.get("channel_id")]),
        )

    async def _default_summarize_recent_context() -> dict[str, Any]:
        history = get_conversation_history(session_key=runtime_session_key(guild_id=guild_id), guild_id=guild_id)
        return build_autonomy_recent_context_payload(history)

    async def _default_maybe_ping_user(text: str) -> dict[str, Any]:
        last_ping_at = float(last_autonomy_ping_at.get(guild_id, 0.0) or 0.0)
        if last_ping_at > 0 and (time.monotonic() - last_ping_at) < 900:
            return {"status": "blocked", "reason": "ping_cooldown"}
        session_key = runtime_session_key(guild_id=guild_id)
        history = get_conversation_history(session_key=session_key, guild_id=guild_id)
        latest_user_text = pick_recent_user_text(history)
        marked = select_and_mark_proactive_question(
            guild_id=guild_id,
            source="autonomy",
            user_text=latest_user_text,
            answer_text="",
            awaiting_user_reply=False,
            session_key=session_key,
            session_memory_key=session_key,
        )
        if not marked:
            return {"status": "ok", "reason": "no_queued_proactive_question", "skipped": True}
        return await _default_send_followup(
            marked["ask_text"],
            awaiting_user_reply=True,
            user_text=latest_user_text or "[autonomy]",
        )

    async def _default_refresh_cognitive_state() -> dict[str, Any]:
        existing = autonomy_cognitive_refresh_tasks.get(guild_id)
        if existing is not None and not existing.done():
            return {"status": "blocked", "reason": "router_refresh_inflight"}
        session_key = runtime_session_key(guild_id=guild_id)
        history = get_conversation_history(session_key=session_key, guild_id=guild_id)
        latest_user_text = pick_recent_user_text(history)
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
    return resolve_runtime_session_key(session_key=session_key, guild_id=guild_id)


def make_text_session_key(guild_id: int, channel_id: int, user_id: int | None = None, thread_id: int | None = None) -> str:
    return make_discord_text_session_key(guild_id, channel_id, user_id, thread_id=thread_id)


def make_text_reply_slot_key(guild_id: int, channel_id: int, thread_id: int | None = None) -> str:
    return make_discord_text_reply_slot_key(guild_id, channel_id, thread_id=thread_id)


def make_voice_room_session_key(guild_id: int, voice_channel_id: int | None) -> str:
    return make_discord_voice_room_session_key(guild_id, voice_channel_id)


def make_voice_session_key(guild_id: int, voice_channel_id: int | None, user_id: int | None = None) -> str:
    return make_discord_voice_session_key(guild_id, voice_channel_id, user_id)


def make_room_memory_key(kind: str, room_id: int | None) -> str:
    return make_discord_room_memory_key(kind, room_id)


def make_person_memory_key(user_id: int | None) -> str | None:
    return make_discord_person_memory_key(user_id)


def make_session_memory_key(session_key: str | None, user_id: int | None = None) -> str | None:
    return make_discord_session_memory_key(session_key, user_id)


def remember_session_followup_target(session_key: str, *, channel_id: int | None = None, message_id: int | None = None) -> None:
    session_state_store.remember_followup_target(session_key, channel_id=channel_id, message_id=message_id)


def build_topic_id(*texts: str) -> str:
    return build_session_topic_id(*texts)


def new_turn_id() -> str:
    return new_session_turn_id()


def current_turn_id(session_key: str | None) -> str | None:
    return session_state_store.current_turn_id(session_key)


def next_segment_id(session_key: str | None) -> int:
    return session_state_store.next_segment_id(session_key)


def start_new_turn(session_key: str | None, *, turn_id: str | None = None) -> str:
    return session_state_store.start_new_turn(session_key, turn_id=turn_id)


def begin_user_text_turn(
    session_key: str,
    user_text: str,
    *,
    guild_id: int | None = None,
    user_id: int | None = None,
) -> Any:
    return session_state_store.begin_user_text_turn(
        session_key,
        user_text,
        system_prompt=SYSTEM_PROMPT,
        active_conversation_awaiting_reply_sec=ACTIVE_CONVERSATION_TEXT_QUESTION_SEC,
        max_history_items=MAX_HISTORY_ITEMS,
        guild_id=guild_id,
        user_id=user_id,
        previous_topic_id=session_topic_ids.get(session_key, ""),
    )


def finish_assistant_text_turn(
    session_key: str,
    user_text: str,
    answer_text: str,
    *,
    guild_id: int | None = None,
    user_id: int | None = None,
    awaiting_user_reply: bool,
    topic_id: str | None = None,
) -> Any:
    return session_state_store.finish_assistant_text_turn(
        session_key,
        user_text,
        answer_text,
        system_prompt=SYSTEM_PROMPT,
        max_history_items=MAX_HISTORY_ITEMS,
        guild_id=guild_id,
        user_id=user_id,
        awaiting_user_reply=awaiting_user_reply,
        normal_ttl_sec=ACTIVE_CONVERSATION_TEXT_SEC,
        question_ttl_sec=ACTIVE_CONVERSATION_TEXT_QUESTION_SEC,
        topic_id=topic_id,
    )


def session_state_snapshot(session_key: str | None) -> dict:
    return session_state_store.snapshot(session_key)


def discord_room_session_policy() -> DiscordRoomSessionPolicy:
    return DiscordRoomSessionPolicy(
        room_owner_user_ids=room_owner_user_ids,
        room_owner_until=room_owner_until,
        room_reply_in_progress=room_reply_in_progress,
        log_event=log_turn_event,
        now_monotonic=time.monotonic,
        pick_active_speaker=pick_active_speaker,
    )


def _clear_room_owner(room_session_key: str | None) -> None:
    discord_room_session_policy().clear_owner(room_session_key)



def room_state_snapshot(room_session_key: str | None) -> dict:
    return discord_room_session_policy().snapshot(room_session_key)



def _prune_room_speaker_stats(room_session_key: str | None, *, now: float | None = None) -> dict[int, dict[str, float]]:
    return room_speaker_activity_store.prune(room_session_key, now=now)



def update_room_speaker_activity(
    room_session_key: str | None,
    user_id: int | None,
    *,
    voiced_ms: float,
    raw_seconds: float,
    rms: float,
    wake_detected: bool = False,
) -> dict[str, float]:
    return room_speaker_activity_store.update(
        room_session_key,
        user_id,
        voiced_ms=voiced_ms,
        raw_seconds=raw_seconds,
        rms=rms,
        wake_detected=wake_detected,
    )



def pick_active_speaker(room_session_key: str | None) -> int | None:
    return room_speaker_activity_store.pick_active_speaker(room_session_key)



def is_room_owner_active(room_session_key: str | None, user_id: int | None) -> bool:
    return discord_room_session_policy().is_owner_active(room_session_key, user_id)



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
    discord_room_session_policy().set_owner(
        room_session_key,
        user_id,
        ttl_sec=ttl_sec,
        reason=reason,
        session_key=session_key,
        turn_id=turn_id,
        segment_id=segment_id,
    )



def set_room_reply_in_progress(room_session_key: str | None, value: bool, *, owner_user_id: int | None = None) -> None:
    discord_room_session_policy().set_reply_in_progress(room_session_key, value, owner_user_id=owner_user_id)



def increment_session_bad_audio(session_key: str | None) -> int:
    return session_state_store.increment_bad_audio(session_key)



def reset_session_bad_audio(session_key: str | None) -> None:
    session_state_store.reset_bad_audio(session_key)


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
    session_state_store.update_session_state(
        session_key,
        user_id=user_id,
        speaker=speaker,
        ttl_sec=ttl_sec,
        awaiting_user_reply=awaiting_user_reply,
        topic_id=topic_id,
        answer_text=answer_text,
        user_text=user_text,
        active_conversation_awaiting_reply_sec=ACTIVE_CONVERSATION_AWAITING_REPLY_SEC,
    )


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
    session_state_store.mark_active(
        session_key,
        user_id=user_id,
        speaker=speaker,
        ttl_sec=ttl_sec,
        awaiting_user_reply=awaiting_user_reply,
        topic_id=topic_id,
        answer_text=answer_text,
        user_text=user_text,
        active_conversation_awaiting_reply_sec=ACTIVE_CONVERSATION_AWAITING_REPLY_SEC,
    )


def is_session_active_for_user(session_key: str, user_id: int | None = None) -> bool:
    return session_state_store.is_active_for_user(session_key, user_id)


def get_conversation_history(*, session_key: str | None = None, guild_id: int | None = None) -> list[dict]:
    return session_state_store.get_conversation_history(
        system_prompt=SYSTEM_PROMPT,
        session_key=session_key,
        guild_id=guild_id,
    )


def trim_history(*, session_key: str | None = None, guild_id: int | None = None) -> None:
    session_state_store.trim_history(
        system_prompt=SYSTEM_PROMPT,
        max_history_items=MAX_HISTORY_ITEMS,
        session_key=session_key,
        guild_id=guild_id,
    )


def append_history(session_key: str | None, user_text: str, answer: str, *, guild_id: int | None = None) -> None:
    session_state_store.append_history(
        session_key,
        user_text,
        answer,
        system_prompt=SYSTEM_PROMPT,
        max_history_items=MAX_HISTORY_ITEMS,
        guild_id=guild_id,
    )


def recent_assistant_reply_summary(*, session_key: str | None = None, guild_id: int | None = None, limit: int = 1) -> str:
    return session_state_store.recent_assistant_reply_summary(
        system_prompt=SYSTEM_PROMPT,
        session_key=session_key,
        guild_id=guild_id,
        limit=limit,
    )


def is_casual_call_or_status_question(text: str) -> bool:
    return session_is_casual_call_or_status_question(text)


def persona_state_hint_for_turn(user_text: str, *, session_key: str | None = None, guild_id: int | None = None) -> str:
    return session_state_store.persona_state_hint_for_turn(
        user_text,
        system_prompt=SYSTEM_PROMPT,
        session_key=session_key,
        guild_id=guild_id,
    )


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
        for room_key, record in list(room_last_voice_utterance_for_merge.items()):
            if record.session_key == key:
                room_last_voice_utterance_for_merge.pop(room_key, None)
        session_partial_stt_text.pop(key, None)
        session_committed_stt_text.pop(key, None)
        session_bad_audio_counts.pop(key, None)
    for key in [key for key in room_owner_user_ids if key.startswith(prefix)]:
        room_owner_user_ids.pop(key, None)
        room_owner_until.pop(key, None)
        room_reply_in_progress.pop(key, None)
        room_last_voice_reply_at.pop(key, None)
    turn_scope_registry.cancel_matching_prefix(prefix)
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


def log_turn_event(event: str, **payload) -> None:
    write_turn_trace_event(
        event,
        payload,
        turn_trace_json_log=TURN_TRACE_JSON_LOG,
        bottleneck_events=_BOTTLENECK_TURN_TRACE_EVENTS,
        summary_events=TURN_SUMMARY_EVENTS,
        console_only_stt_and_reply=VOICE_CONSOLE_ONLY_STT_AND_REPLY,
        voice_bottleneck_logs=VOICE_BOTTLENECK_LOGS,
        voice_trace_all_events=VOICE_TRACE_ALL_EVENTS,
        log_dir=TURN_TRACE_LOG_DIR,
        file_lock=turn_trace_file_lock,
        original_print=_ORIGINAL_PRINT,
        trace_print=print,
    )


def record_model_call_trace(
    *,
    model_role: str,
    purpose: str,
    hot_path: bool,
    started_at: float,
    success: bool,
    metrics: dict | None = None,
    first_token_ms: float | None = None,
    error: BaseException | str | None = None,
    model_name: str | None = None,
    endpoint: str | None = None,
    turn_id: str | None = None,
    session_key: str | None = None,
    source: str | None = None,
    guild_id: int | None = None,
) -> None:
    meta = (metrics or {}).get("meta") if isinstance(metrics, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    elapsed_ms = max(0.0, (time.monotonic() - float(started_at)) * 1000.0)
    error_text = repr(error) if isinstance(error, BaseException) else clean_text(str(error or ""))
    record_model_call_metric(
        model_role=model_role,
        purpose=purpose,
        hot_path=hot_path,
        success=success,
        latency_ms=elapsed_ms,
        first_token_ms=first_token_ms,
    )
    log_turn_event(
        "model_call",
        model_role=clean_text(model_role),
        purpose=clean_text(purpose),
        hot_path=bool(hot_path),
        success=bool(success),
        latency_ms=round(elapsed_ms, 1),
        first_token_ms=None if first_token_ms is None else round(float(first_token_ms), 1),
        model_name=clean_text(model_name or ""),
        endpoint=clean_text(endpoint or ""),
        turn_id=turn_id or meta.get("turn_id"),
        session_key=session_key or meta.get("session_key"),
        source=source or meta.get("source"),
        guild_id=guild_id if guild_id is not None else meta.get("guild_id"),
        error=clean_text(error_text)[:240] if error_text else None,
    )


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


def replace_room_turn_scope(room_id: str, new_scope: TurnScope, *, cancel_old: bool = True) -> TurnScope | None:
    return turn_scope_registry.replace_room_scope(room_id, new_scope, cancel_old=cancel_old)


def get_room_turn_scope(room_id: str | None) -> TurnScope | None:
    return turn_scope_registry.get_room_scope(room_id)


def _attach_current_task(turn_scope: TurnScope | None) -> asyncio.Task | None:
    return turn_scope_registry.attach_current_task(turn_scope)


def _detach_task(turn_scope: TurnScope | None, task: asyncio.Task | None) -> None:
    turn_scope_registry.detach_task(turn_scope, task)


def create_turn_scoped_task(coro: Awaitable[Any], turn_scope: TurnScope | None = None) -> asyncio.Task:
    return turn_scope_registry.create_scoped_task(coro, turn_scope=turn_scope)


def clear_room_turn_scope(room_id: str | None, turn_scope: TurnScope | None = None) -> None:
    turn_scope_registry.clear_room_scope(room_id, turn_scope)


def record_turn_stage(turn_id: str | None, stage: str, elapsed_ms: float) -> None:
    if not turn_id or not stage:
        return
    stages = turn_stage_metrics.setdefault(turn_id, {})
    stages[stage] = float(elapsed_ms)


def record_model_call_metric(
    *,
    model_role: str,
    purpose: str,
    hot_path: bool,
    success: bool,
    latency_ms: float,
    first_token_ms: float | None = None,
) -> None:
    model_call_metrics_store.record_model_call(
        model_role=model_role,
        purpose=purpose,
        hot_path=hot_path,
        success=success,
        latency_ms=latency_ms,
        first_token_ms=first_token_ms,
    )


def replay_model_call_metrics_from_turn_trace(*, max_files: int = 7, max_lines_per_file: int = 12000) -> dict[str, int]:
    return model_call_metrics_store.replay_model_calls_from_turn_trace(
        max_files=max_files,
        max_lines_per_file=max_lines_per_file,
    )


def ensure_model_call_metrics_replayed() -> None:
    model_call_metrics_store.ensure_replayed()


def record_turn_path_summary(meta: dict[str, Any], marks: dict[str, Any], total_ms: float) -> None:
    model_call_metrics_store.record_turn_path_summary(meta, marks, total_ms)


def summarize_turn_path_metrics() -> list[dict[str, Any]]:
    return model_call_metrics_store.summarize_turn_paths()


def summarize_model_call_metrics() -> dict[str, Any]:
    return model_call_metrics_store.summarize_model_calls()


def normalize_question_policy_mapping(value: dict[str, Any] | None, *, default_source: str = "none") -> dict[str, Any]:
    return normalize_question_policy_mapping_payload(value, default_source=default_source)


def extract_question_policy_from_route_meta(route_meta: dict[str, Any] | None) -> dict[str, Any]:
    return extract_question_policy_from_route_meta_payload(route_meta)


def user_wants_direct_answer(text: str) -> bool:
    return user_wants_direct_answer_payload(text)


def user_frustration_with_questions(text: str) -> bool:
    return user_frustration_with_questions_payload(text)


def is_continuable_technical_topic(text: str) -> bool:
    return is_continuable_technical_topic_payload(text)


def question_cooldown_hit(session_key: str | None, *, now: float | None = None) -> bool:
    return question_policy_state.question_cooldown_hit(session_key, now=now)


def apply_fast_path_question_policy(
    route_decision: RouteDecision,
    *,
    user_text: str,
    session_key: str | None,
    route_meta_question_policy: dict[str, Any] | None = None,
) -> tuple[RouteDecision, bool]:
    return question_policy_state.apply_fast_path_policy(
        route_decision,
        user_text=user_text,
        session_key=session_key,
        route_meta_question_policy=route_meta_question_policy,
    )


def record_question_trace(
    *,
    route_decision: RouteDecision,
    answer: str,
    shape_meta: dict[str, Any],
    metrics: dict | None,
    cooldown_hit: bool = False,
) -> None:
    question_policy_state.record_question_trace(
        route_decision=route_decision,
        answer=answer,
        shape_meta=shape_meta,
        metrics=metrics,
        cooldown_hit=cooldown_hit,
    )


def summarize_question_metrics() -> dict[str, Any]:
    return question_policy_state.summarize_question_metrics()


def proactive_question_scope_candidates(
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> list[tuple[str, str | None]]:
    return question_policy_state.proactive_scope_candidates(
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )


def record_session_question_asked(session_key: str | None, *, now: float | None = None) -> None:
    question_policy_state.record_session_question_asked(session_key, now=now)


def resolve_pending_proactive_question_for_turn(
    guild_id: int | None,
    user_text: str,
    *,
    session_key: str | None = None,
    session_memory_key: str | None = None,
    metrics: dict | None = None,
) -> dict[str, Any]:
    return question_policy_state.resolve_pending_proactive_question_for_turn(
        guild_id,
        user_text,
        session_key=session_key,
        session_memory_key=session_memory_key,
        metrics=metrics,
    )


def select_and_mark_proactive_question(
    *,
    guild_id: int | None,
    source: str,
    user_text: str,
    answer_text: str = "",
    awaiting_user_reply: bool = False,
    room_key: str | None = None,
    person_key: str | None = None,
    session_key: str | None = None,
    session_memory_key: str | None = None,
    runtime_block_reason: str = "",
    metrics: dict | None = None,
) -> dict[str, Any] | None:
    return question_policy_state.select_and_mark_proactive_question(
        guild_id=guild_id,
        source=source,
        user_text=user_text,
        answer_text=answer_text,
        awaiting_user_reply=awaiting_user_reply,
        room_key=room_key,
        person_key=person_key,
        session_key=session_key,
        session_memory_key=session_memory_key,
        runtime_block_reason=runtime_block_reason,
        metrics=metrics,
    )


def maybe_append_proactive_question(
    answer_text: str,
    *,
    guild_id: int | None,
    source: str,
    user_text: str,
    awaiting_user_reply: bool,
    room_key: str | None = None,
    person_key: str | None = None,
    session_key: str | None = None,
    session_memory_key: str | None = None,
    metrics: dict | None = None,
) -> tuple[str, bool]:
    return question_policy_state.maybe_append_proactive_question(
        answer_text,
        guild_id=guild_id,
        source=source,
        user_text=user_text,
        awaiting_user_reply=awaiting_user_reply,
        room_key=room_key,
        person_key=person_key,
        session_key=session_key,
        session_memory_key=session_memory_key,
        metrics=metrics,
    )


def summarize_p95_metrics() -> dict[str, float | int]:
    return summarize_voice_p95_metrics(
        turn_stage_metrics,
        search_followup_queued_count=search_followup_queued_count,
        cancelled_stale_turn_count=turn_scope_registry.cancelled_stale_turn_count,
    )


def increment_voice_pipeline_counter(name: str, amount: int = 1) -> None:
    increment_voice_counter(voice_pipeline_counters, name, amount)


voice_barge_in_continuity_tracker = VoiceBargeInContinuityTracker(
    target_count=VOICE_BARGE_IN_CONTINUITY_TARGET,
    clean_text=clean_text,
    log_enabled=lambda: VOICE_BOTTLENECK_LOGS,
    event_logger=log_turn_event,
)


def _parse_barge_in_reason_label(raw_reason_code: str) -> str:
    return voice_barge_in_continuity_tracker.parse_reason_label(raw_reason_code)


def _format_voice_barge_in_continuity_summary(continuity: dict[str, Any]) -> str:
    return voice_barge_in_continuity_tracker.format_summary(continuity)


def _format_voice_barge_in_continuity_detail_lines(continuity: dict[str, Any]) -> list[str]:
    return voice_barge_in_continuity_tracker.format_detail_lines(continuity, command_status=command_status)


def start_voice_barge_in_continuity_probe(metrics: dict, *, source: str) -> None:
    voice_barge_in_continuity_tracker.start_probe(metrics, source=source)


def _build_voice_barge_in_continuity_snapshot() -> dict[str, Any]:
    return voice_barge_in_continuity_tracker.snapshot()


def reset_voice_barge_in_continuity_probe(*, reason: str = "") -> None:
    voice_barge_in_continuity_tracker.reset(reason=reason)


def _mark_voice_barge_in_continuity_probe(
    metrics: dict,
    *,
    success: bool,
    reason: str,
    queued_sentence_count: int = 0,
    reason_code: str | None = None,
    reason_label: str | None = None,
    event: str = VOICE_BARGE_IN_EVENT_FINISH,
) -> None:
    voice_barge_in_continuity_tracker.mark_probe(
        metrics,
        success=success,
        reason=reason,
        queued_sentence_count=queued_sentence_count,
        reason_code=reason_code,
        reason_label=reason_label,
        event=event,
    )


def get_stt_inference_lock() -> asyncio.Lock:
    global stt_inference_lock
    if stt_inference_lock is None:
        stt_inference_lock = asyncio.Lock()
    return stt_inference_lock


def voice_last_channel_state_path() -> Path:
    return resolve_voice_last_channel_state_path(PROJECT_ROOT, VOICE_LAST_CHANNEL_STATE_FILE)


def load_last_voice_channel_state() -> dict[str, Any]:
    return load_last_voice_channel_state_payload(PROJECT_ROOT, VOICE_LAST_CHANNEL_STATE_FILE)


def save_last_voice_channel_state(
    guild: discord.Guild,
    channel: discord.VoiceChannel,
    *,
    reason: str,
    manual_disconnect: bool = False,
) -> None:
    try:
        save_last_voice_channel_state_payload(
            PROJECT_ROOT,
            VOICE_LAST_CHANNEL_STATE_FILE,
            voice_pipeline_state,
            guild,
            channel,
            reason=reason,
            manual_disconnect=manual_disconnect,
        )
    except Exception as exc:
        print(f"[VOICE STATE SAVE FAIL] err={exc!r}")


def mark_voice_manual_disconnect(guild: discord.Guild | None, *, reason: str) -> None:
    try:
        mark_last_voice_manual_disconnect(
            PROJECT_ROOT,
            VOICE_LAST_CHANNEL_STATE_FILE,
            voice_pipeline_state,
            guild,
            reason=reason,
        )
    except Exception as exc:
        print(f"[VOICE STATE SAVE FAIL] err={exc!r}")


def record_voice_pipeline_failure(kind: str, err: BaseException | str, metrics: dict | None = None, **extra: Any) -> None:
    error_text = record_voice_failure_state(voice_pipeline_counters, voice_pipeline_state, kind, err)
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
    state_file = load_last_voice_channel_state()
    output_mode = "local_speaker" if LOCAL_ONLY_MODE and local_tts_playback_manager.enabled else "discord_voice"
    return build_voice_pipeline_snapshot_payload(
        counters=voice_pipeline_counters,
        state=voice_pipeline_state,
        p95=p95,
        now_time=time.time(),
        now_mono=time.monotonic(),
        stt_lock_locked=bool(lock and lock.locked()),
        stt_cooldown_until=stt_cooldown_until,
        last_channel_state=state_file,
        output_mode=output_mode,
        local_tts_output=local_tts_playback_manager.snapshot(),
        queue_depth=voice_ingress_queue.qsize(),
        queue_max=VOICE_INGRESS_QUEUE_MAX,
        live_recent_sec=VOICE_LIVE_RECENT_SEC,
        utterance_assembly_enabled=voice_utterance_assembly_config.enabled,
        utterance_pending_count=len(voice_utterance_buffers),
        utterance_commit_wait_sec=voice_utterance_assembly_config.commit_wait_sec,
        barge_in_continuity=_build_voice_barge_in_continuity_snapshot(),
        turn_path_metrics=summarize_turn_path_metrics(),
    )


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
    save_voice_debug_audio_now_payload(
        project_root=PROJECT_ROOT,
        configured_dir=VOICE_DEBUG_AUDIO_DIR,
        max_files_per_guild=VOICE_DEBUG_MAX_FILES_PER_GUILD,
        raw_channels=CHANNELS,
        raw_rate=RATE,
        stt_rate=TARGET_RATE,
        counts=voice_debug_counts,
        stems=voice_debug_stems,
        log=print,
        guild_id=guild_id,
        speaker=speaker,
        pcm_bytes=pcm_bytes,
        audio16k=audio16k,
        wake_probe=wake_probe,
        final_text=final_text,
        debug_meta=debug_meta,
        save_stt_audio=save_stt_audio,
        stt_meta=stt_meta,
        session_key=session_key,
        stage_label=stage_label,
    )


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
    item = build_voice_debug_audio_item(
        guild_id=guild_id,
        speaker=speaker,
        pcm_bytes=pcm_bytes,
        audio16k=audio16k,
        wake_probe=wake_probe,
        final_text=final_text,
        debug_meta=debug_meta,
        save_stt_audio=save_stt_audio,
        stt_meta=stt_meta,
        session_key=session_key,
        stage_label=stage_label,
    )
    try:
        debug_write_queue.put_nowait(item)
    except asyncio.QueueFull:
        print(voice_debug_drop_message(speaker=speaker, stage_label=stage_label))


def estimate_voice_like_probability(*, voiced_ms: float, audio_sec: float, body_rms: float) -> float:
    audio_ms = max(audio_sec * 1000.0, 1.0)
    voiced_ratio = max(0.0, min(1.0, voiced_ms / audio_ms))
    rms_ratio = 0.0
    if VOICE_WAVEFORM_BODY_RMS_MIN > 0:
        rms_ratio = max(0.0, min(1.0, body_rms / VOICE_WAVEFORM_BODY_RMS_MIN))
    return max(voiced_ratio, rms_ratio)


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

FAST_PATH_NEGATED_SEARCH_MARKERS = (
    "검색 없이",
    "검색은 하지 말고",
    "검색하지 말고",
    "검색하지마",
    "인터넷 없이",
    "웹 없이",
    "찾지 말고",
    "찾아보지 말고",
    "without search",
    "without searching",
    "no search",
    "don't search",
    "do not search",
    "without looking up",
)

FAST_PATH_SEARCH_ROUTE_MARKERS = (
    "검색",
    "찾아봐",
    "찾아 봐",
    "찾아",
)

CONTROL_PAGE_LIGHT_REQUEST_MAX_CHARS = 180


def is_control_page_source(source: str) -> bool:
    return clean_text(source).lower() in {"control_page", "control-page", "local_control_page"}


def deep_route_marker_count(text: str, *, ignore_search_markers: bool = False) -> int:
    cleaned = clean_text(text)
    ignored_markers = set(FAST_PATH_SEARCH_ROUTE_MARKERS) if ignore_search_markers else set()
    return sum(1 for marker in FAST_PATH_DEEP_ROUTE_MARKERS if marker not in ignored_markers and marker in cleaned)


def has_negated_search_marker(text: str) -> bool:
    cleaned = clean_text(text).lower()
    compact = re.sub(r"\s+", "", cleaned)
    return any(marker in cleaned for marker in FAST_PATH_NEGATED_SEARCH_MARKERS) or any(
        marker.replace(" ", "") in compact for marker in FAST_PATH_NEGATED_SEARCH_MARKERS
    )


def needs_search_or_deep_routing(text: str, *, source: str = "text") -> bool:
    cleaned = clean_text(text)
    if not cleaned:
        return False
    negated_search = has_negated_search_marker(cleaned)
    if not negated_search and should_force_search_query(cleaned):
        return True
    marker_hits = deep_route_marker_count(cleaned, ignore_search_markers=negated_search)
    search_markers = ("검색", "찾아", "최신", "뉴스", "시세", "가격", "주가", "환율")
    if not negated_search and any(marker in cleaned for marker in search_markers):
        return True
    if (
        is_control_page_source(source)
        and marker_hits == 0
        and len(cleaned) <= CONTROL_PAGE_LIGHT_REQUEST_MAX_CHARS
    ):
        return False
    if marker_hits >= 2:
        return True
    if len(cleaned) >= 72:
        return True
    return False


def is_simple_directive(text: str, *, source: str = "text") -> bool:
    cleaned = clean_text(text)
    if not cleaned:
        return False
    if needs_search_or_deep_routing(cleaned, source=source):
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
    if is_control_page_source(source):
        return None
    if is_obvious_continue(cleaned, source, room_state):
        return {"route": "main_direct", "action": "wait", "reason_brief": "obvious_continue"}
    if should_force_search_query(cleaned):
        return {"route": "search_executor", "action": "search_then_answer", "reason_brief": "search_trigger"}
    if is_simple_directive(cleaned, source=source):
        return {"route": "main_direct", "action": "answer", "reason_brief": "simple_directive"}
    if not needs_search_or_deep_routing(cleaned, source=source):
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


def should_ignore_short_transcription(
    text: str,
    pcm_bytes: bytes,
    *,
    wake_detected: bool = False,
) -> bool:
    audio_sec = len(pcm_bytes) / (RATE * CHANNELS * 2)
    return should_ignore_short_transcription_policy(
        text=text,
        audio_sec=audio_sec,
        wake_detected=wake_detected,
        normalize_voice_text=normalize_voice_text,
        normalized_wake_words=normalized_wake_words,
        min_audio_sec=MIN_AUDIO_SEC,
        min_transcribed_len=MIN_TRANSCRIBED_LEN,
        wake_short_text_keep_len=WAKE_SHORT_TEXT_KEEP_LEN,
    )


def is_short_followup_candidate(
    text: str,
    pcm_bytes: bytes,
    *,
    wake_detected: bool = False,
    owner_followup_active: bool = False,
) -> bool:
    audio_sec = len(pcm_bytes) / (RATE * CHANNELS * 2)
    return is_short_followup_candidate_policy(
        text=text,
        audio_sec=audio_sec,
        wake_detected=wake_detected,
        owner_followup_active=owner_followup_active,
        normalize_voice_text=normalize_voice_text,
        min_audio_sec=MIN_AUDIO_SEC,
        min_transcribed_len=MIN_TRANSCRIBED_LEN,
    )


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
    ignore_tts_suppression: bool = False,
) -> tuple[bool, str, str]:
    now = time.monotonic()
    session_state = session_state_snapshot(session_key)
    room_state = room_state_snapshot(room_session_key)
    owner_user_id = room_state.get("owner_user_id")
    owner_active = is_room_owner_active(room_session_key, user_id)
    active_session = session_key is not None and is_session_active_for_user(session_key, user_id)
    if active_speaker_user_id is None:
        active_speaker_user_id = room_state.get("active_speaker_user_id")

    tts_suppression = None
    if not ignore_tts_suppression:
        tts_suppression = tts_playback_manager.input_suppression_reason(
            guild_id=guild_id,
            post_tts_ignore_sec=POST_TTS_IGNORE_SEC,
            now=now,
        )
    cooldown_active = bool(room_session_key and (now - room_last_voice_reply_at.get(room_session_key, 0.0) < REPLY_COOLDOWN_SEC))
    decision = decide_voice_reply_gate(
        VoiceReplyGateInput(
            text=text,
            wake_detected=wake_detected,
            wake_match_mode=wake_match_mode,
            user_id=user_id,
            owner_user_id=owner_user_id,
            owner_active=owner_active,
            active_session=active_session,
            awaiting_user_reply=bool(session_state.get("awaiting_user_reply")),
            active_speaker_user_id=active_speaker_user_id,
            last_stt_text=str(session_state.get("last_stt_text", "")),
            tts_suppression=tts_suppression,
            cooldown_active=cooldown_active,
        ),
        normalize_voice_text=normalize_voice_text,
        contains_wake_word=contains_wake_word,
        looks_like_brief_filler_text=looks_like_brief_filler_text,
        looks_like_repetitive_noise_text=looks_like_repetitive_noise_text,
        is_similar=is_similar,
        min_text_len=MIN_TEXT_LEN,
    )
    return decision.accepted, decision.reason, decision.gate_mode


def should_skip_full_stt_after_wake_probe(*, wake_detected: bool, wake_probe: str, duration_sec: float) -> bool:
    return should_skip_full_stt_after_wake_probe_policy(
        wake_detected=wake_detected,
        wake_probe=wake_probe,
        duration_sec=duration_sec,
        no_wake_max_continue_sec=VOICE_NO_WAKE_MAX_CONTINUE_SEC,
        clean_text=clean_text,
        looks_like_brief_filler_text=looks_like_brief_filler_text,
        looks_like_repetitive_noise_text=looks_like_repetitive_noise_text,
    )


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


def _voice_utterance_buffer_key(item: dict[str, Any]) -> str:
    return str(item.get("session_key") or "")


async def _enqueue_voice_ingress_for_processing(item: dict[str, Any]) -> None:
    debug_meta = item.get("debug_meta")
    if isinstance(debug_meta, dict):
        debug_meta["voice_queue_depth_at_enqueue"] = voice_ingress_queue.qsize()
    item["enqueued_at"] = time.monotonic()
    enqueue_result = enqueue_voice_ingress_item(
        voice_ingress_queue,
        item,
        drop_oldest_on_full=VOICE_INGRESS_DROP_OLDEST_ON_FULL,
    )
    if not enqueue_result.accepted:
        increment_voice_pipeline_counter("queue_full_drop_count")
        member = item.get("member")
        print(
            f"[VOICE QUEUE DROP] reason=queue_full speaker={getattr(member, 'display_name', None)} "
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


async def _flush_voice_utterance_buffer(key: str) -> None:
    buffer = voice_utterance_buffers.pop(key, None)
    voice_utterance_flush_tasks.pop(key, None)
    if not buffer:
        return
    base_item = dict(buffer["base_item"])
    segments = list(buffer.get("segments") or [])
    merged_pcm = merge_discord_pcm_segments(segments, pad_ms=voice_utterance_assembly_config.pad_ms)
    if not merged_pcm:
        return
    segment_count = len(segments)
    base_item["pcm_bytes"] = merged_pcm
    base_item["ingress_during_reply"] = bool(buffer.get("ingress_during_reply"))
    base_item["owner_user_id_on_ingress"] = buffer.get("owner_user_id_on_ingress")
    base_meta = dict(base_item.get("debug_meta") or {})
    base_meta["assembled_segment_ids"] = list(buffer.get("segment_ids") or [])
    base_item["debug_meta"] = merge_debug_meta(
        base_meta,
        segment_count=segment_count,
        added_pad_ms=max(0, segment_count - 1) * voice_utterance_assembly_config.pad_ms,
        total_audio_sec=discord_pcm_seconds(merged_pcm),
    )
    increment_voice_pipeline_counter("utterance_assembly_flush_count")
    if segment_count > 1:
        increment_voice_pipeline_counter("utterance_assembly_merge_count")
        print(f"[VOICE UTTERANCE MERGE] session={key} segments={segment_count} sec={discord_pcm_seconds(merged_pcm):.2f}")
    await _enqueue_voice_ingress_for_processing(base_item)


async def _delayed_voice_utterance_flush(key: str, delay_sec: float) -> None:
    try:
        await asyncio.sleep(max(0.0, delay_sec))
        await _flush_voice_utterance_buffer(key)
    except asyncio.CancelledError:
        pass


async def _schedule_voice_utterance_item(item: dict[str, Any]) -> None:
    if not voice_utterance_assembly_config.enabled or voice_utterance_assembly_config.commit_wait_sec <= 0.0:
        await _enqueue_voice_ingress_for_processing(item)
        return

    key = _voice_utterance_buffer_key(item)
    if not key:
        await _enqueue_voice_ingress_for_processing(item)
        return

    existing_task = voice_utterance_flush_tasks.pop(key, None)
    if existing_task is not None and not existing_task.done():
        existing_task.cancel()

    pcm_bytes = bytes(item.get("pcm_bytes") or b"")
    buffer = voice_utterance_buffers.get(key)
    if buffer is None:
        buffer = {
            "base_item": dict(item),
            "segments": [],
            "segment_ids": [],
            "ingress_during_reply": bool(item.get("ingress_during_reply")),
            "owner_user_id_on_ingress": item.get("owner_user_id_on_ingress"),
        }
        voice_utterance_buffers[key] = buffer

    buffer["segments"].append(pcm_bytes)
    buffer["segment_ids"].append(item.get("segment_id"))
    buffer["ingress_during_reply"] = bool(buffer.get("ingress_during_reply") or item.get("ingress_during_reply"))
    if buffer.get("owner_user_id_on_ingress") is None:
        buffer["owner_user_id_on_ingress"] = item.get("owner_user_id_on_ingress")

    current_sec = sum(discord_pcm_seconds(segment) for segment in buffer.get("segments") or [])
    if current_sec >= voice_utterance_assembly_config.max_audio_sec:
        await _flush_voice_utterance_buffer(key)
        return

    voice_utterance_flush_tasks[key] = asyncio.create_task(
        _delayed_voice_utterance_flush(key, voice_utterance_assembly_config.commit_wait_sec)
    )


def should_label_question_response(text: str, *, session_key: str | None = None) -> bool:
    visible = normalize_friend_style_output(visible_text(text)).strip()
    if not visible:
        return False
    if visible.startswith("[질문]"):
        return False
    if session_key is not None and session_state_snapshot(session_key).get("awaiting_user_reply"):
        return True
    return False


def cleanup_assistant_display_artifacts(text: str) -> str:
    return cleanup_assistant_display_artifacts_payload(text)


def user_explicitly_mentions_minecraft(user_text: str) -> bool:
    return user_explicitly_mentions_minecraft_payload(user_text)


def answer_contains_minecraft_leak(answer: str) -> bool:
    return answer_contains_minecraft_leak_payload(answer)


def fallback_for_unrequested_minecraft_leak(user_text: str) -> str:
    return fallback_for_unrequested_minecraft_leak_payload(
        user_text,
        gpu_status_answer_fn=answer_gpu_runtime_status_query,
    )


def sanitize_unrequested_minecraft_leak(user_text: str, answer: str) -> str:
    return sanitize_unrequested_minecraft_leak_payload(
        user_text,
        answer,
        gpu_status_answer_fn=answer_gpu_runtime_status_query,
    )


def answer_simple_local_chat_query(user_text: str) -> str | None:
    return answer_simple_local_chat_query_payload(user_text)


def format_display_text(text: str, *, session_key: str | None = None) -> str:
    return format_display_text_payload(
        text,
        session_key=session_key,
        should_label_question_response_fn=should_label_question_response,
    )


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
        service_down_labels: list[str] = []
        for result in results:
            if isinstance(result, tuple) and len(result) == 2:
                label, ok = result
                status_parts.append(f"{label}={'up' if ok else 'down'}")
                if not ok:
                    service_down_labels.append(label)

        service_summary = ""
        try:
            services = await get_control_page_runtime_services()
            service_summary = compact_runtime_error(services.get("summary"), max_chars=120)
            bot_api_ready = is_control_api_ready_from_runtime_services(services)
            if services and not bot_api_ready:
                bot_api_reason = clean_text(str(services.get("botApiReason") or services.get("botApiState") or "unknown"))
                service_down_labels.append("bot_api" if not bot_api_reason else f"bot_api:{bot_api_reason}")
        except Exception:
            service_summary = ""
        if service_summary:
            status_parts.append(f"summary={service_summary}")

        gpu_status, gpu_near_full = await asyncio.to_thread(load_runtime_gpu_status)
        if gpu_status:
            status_parts.append("current_gpu_snapshot=" + gpu_status)
        oom_signal = "yes" if gpu_near_full or service_down_labels else "no"
        oom_reason = []
        if gpu_near_full:
            oom_reason.append("gpu_near_full")
        if service_down_labels:
            oom_reason.append("service_down=" + ",".join(service_down_labels[:4]))
        status_parts.append("current_oom_signal=" + oom_signal + (f" ({'; '.join(oom_reason)})" if oom_reason else ""))

        recent_errors = load_runtime_recent_errors()
        if recent_errors:
            status_parts.append("recent_errors=" + " | ".join(recent_errors))
            status_parts.append(
                "recent_errors_are_historical=true; do_not_claim_current_oom_from_recent_errors_without_current_oom_signal=yes"
            )
        else:
            status_parts.append("recent_errors=none")

        text = "; ".join(part for part in status_parts if part)
        runtime_status_context_cache["text"] = text
        runtime_status_context_cache["cached_at"] = time.time()
        return text


def _skill_route_available(route_name: str, *, source: str) -> bool:
    try:
        return bool(skill_registry.find_by_route(route_name, source=source))
    except Exception:
        return False


def build_main_response_guidance(
    cognitive_state: dict | None = None,
    *,
    source: str = "text",
    user_text: str = "",
    session_key: str | None = None,
    guild_id: int | None = None,
    minecraft_state: dict[str, Any] | None = None,
    runtime_status_context: str | None = None,
    route_decision: RouteDecision | None = None,
) -> str:
    state = apply_ask_gating(cognitive_state, source=source)
    parts = [
        "응답 규칙: 짧게 바로 답해라. 이 규칙을 설명하거나 언급하지 마라.",
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
    elif action == "wait":
        parts.append("길게 답하지 말고 더 들을 여지를 둬라.")
    else:
        parts.append("바로 답해라.")

    if runtime_status_context:
        parts.append(f"현재 Evelyn 런타임 상태 요약: {runtime_status_context}")
        parts.append("사용자가 Evelyn의 상태, 오류, 연결, 지연, 서버 상황을 물을 때만 이 런타임 상태를 근거로 답해라. 일반 대화에서는 먼저 꺼내지 마라.")

    if runtime_status_context:
        parts.append(
            "RUNTIME_STATUS_RULE: Use `current_gpu_snapshot` first, including exact GPU names and used/total VRAM. "
            "If `current_oom_signal=no`, do not say current OOM. "
            "If `recent_errors_are_historical=true`, treat recent_errors as historical logs, not proof of current OOM."
        )

    tool_awareness_context = build_tool_awareness_context(
        user_text,
        source=source,
        route_decision=route_decision,
        route_available=_skill_route_available,
    )
    if tool_awareness_context:
        parts.append(tool_awareness_context)

    minecraft_summary = format_minecraft_state_summary(minecraft_state)
    if minecraft_summary:
        parts.append(f"현재 마인크래프트 실시간 상태: {minecraft_summary}")
        parts.append("마인크래프트 관련 질문이나 계획을 답할 때는 이 실시간 상태를 기준으로 말해라. 모르면 추측하지 말고 현재 상태 기준으로 짧게 설명해라.")

    if route_decision is not None:
        ask_mode = clean_text(route_decision.ask_mode)
        max_questions = max(0, min(1, int(route_decision.max_question_count or 0)))
        if QUESTION_FEATURE_ENABLED and ask_mode != "none" and max_questions > 0:
            hint = clean_text(route_decision.question_hint or "")
            reason = clean_text(route_decision.question_reason or "")
            question_parts = [
                "먼저 답변한다.",
                "질문은 마지막에 최대 1개만 자연스럽게 둔다.",
                "억지로 묻지 않는다.",
                "흐름상 질문이 부자연스러우면 생략한다.",
                "사용자가 이미 준 조건을 다시 묻지 않는다.",
            ]
            if hint:
                question_parts.append(f"질문 방향: {hint}")
            if reason:
                question_parts.append(f"질문이 필요한 이유: {reason}")
            parts.append(" ".join(question_parts))
        else:
            parts.append("답변 끝에 새 질문을 덧붙이지 마라.")

    return " ".join(clean_text(part) for part in parts if clean_text(part))


def build_vision_observation_prompt(user_text: str) -> str:
    request = clean_text(user_text)[:240]
    return (
        "Look at this local screen capture for Evelyn. "
        "Answer in concise Korean. Describe the visible scene and include clear UI/OCR text. "
        "Do not guess hidden state. User request: "
        + request
    )


def _capture_local_screen_sync() -> tuple[Path, tuple[int, int]]:
    from PIL import ImageGrab

    VISION_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    image = ImageGrab.grab(all_screens=VISION_CAPTURE_ALL_SCREENS).convert("RGB")
    path = VISION_SCREENSHOT_DIR / f"screen_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.png"
    image.save(path)
    extrema = image.getextrema()
    if extrema and all(int(high) <= 2 for _low, high in extrema):
        raise RuntimeError(f"screen capture returned a black frame: {path}")
    return path, image.size


async def capture_local_screen() -> tuple[Path, tuple[int, int]]:
    return await asyncio.to_thread(_capture_local_screen_sync)


def _delete_file_quietly(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        resolved = path.resolve()
        screenshot_root = VISION_SCREENSHOT_DIR.resolve()
        if screenshot_root not in (resolved, *resolved.parents):
            return False
        if not resolved.exists() or not resolved.is_file():
            return False
        resolved.unlink()
        return True
    except Exception:
        return False


def delete_request_vision_image(path: Path | None) -> bool:
    if not VISION_DELETE_REQUEST_IMAGES:
        return False
    return _delete_file_quietly(path)


def format_vision_observation(
    *,
    image_path: Path,
    image_size: tuple[int, int],
    data: dict[str, Any],
    image_deleted: bool = False,
) -> str:
    scene = clean_text(str(data.get("scene") or ""))
    ocr = clean_text(str(data.get("ocr") or ""))
    ocr_error = clean_text(str(data.get("ocr_error") or ""))
    quality = build_vision_quality(data)
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


async def build_live_vision_context(user_text: str, *, metrics: dict | None = None) -> str:
    if not VISION_AUTO_CAPTURE_ENABLED:
        return "Local screen vision was requested, but automatic capture is disabled."
    started_at = time.monotonic()
    try:
        image_path, image_size = await capture_local_screen()
    except Exception as exc:
        error = clean_text(repr(exc))[:240]
        if metrics is not None:
            metrics.setdefault("meta", {})["vision_capture_error"] = error
        if "black frame" in error.lower():
            return (
                "Local screen vision was requested, but the Windows screen capture returned a black frame. "
                "Do not claim the screen was analyzed. Tell the user the capture itself is black and needs capture-session fixing. "
                f"capture_error: {error}"
            )
        return f"Local screen vision was requested, but screen capture failed: {error}"

    payload = {
        "image_path": str(image_path),
        "prompt": build_vision_observation_prompt(user_text),
        "run_ocr": True,
        "ocr_category": "plain",
        "max_new_tokens": 128,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=VISION_ANALYZE_TIMEOUT_SEC)
        session = await get_http_session()
        async with session.post(f"{VISION_SERVICE_URL.rstrip('/')}/v1/vision/analyze", json=payload, timeout=timeout) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(f"vision service {resp.status}: {error_text[:240]}")
            data = await resp.json()
    except Exception as exc:
        error = clean_text(repr(exc))[:240]
        deleted = delete_request_vision_image(image_path)
        if metrics is not None:
            metrics.setdefault("meta", {})["vision_analyze_error"] = error
            metrics.setdefault("meta", {})["vision_capture_path"] = "" if deleted else str(image_path)
            metrics.setdefault("meta", {})["vision_capture_deleted"] = deleted
        if deleted:
            return f"Local screen capture was discarded after vision analysis failed: {error}"
        return f"Local screen capture was saved at {image_path}, but vision analysis failed: {error}"

    deleted = delete_request_vision_image(image_path)
    observation = format_vision_observation(image_path=image_path, image_size=image_size, data=data, image_deleted=deleted)
    quality = build_vision_quality(data)
    if metrics is not None:
        metrics.setdefault("marks", {})["vision_ready"] = (time.monotonic() - started_at) * 1000.0
        metrics.setdefault("meta", {})["vision_capture_path"] = "" if deleted else str(image_path)
        metrics.setdefault("meta", {})["vision_capture_deleted"] = deleted
        metrics.setdefault("meta", {})["vision_ocr_chars"] = len(clean_text(str(data.get("ocr") or "")))
        metrics.setdefault("meta", {})["vision_scene_chars"] = len(clean_text(str(data.get("scene") or "")))
        metrics.setdefault("meta", {})["vision_quality"] = dict(quality)
    return observation


def build_vision_watch_prompt() -> str:
    return (
        "You are Evelyn's lightweight background screen observer. "
        "Describe only clearly visible changes on the user's local screen in Korean. "
        "Be concise. Mention app/window/menu/error/text only if visible. "
        "Do not infer Minecraft bot inventory or bot state from this user screen."
    )


def vision_watch_scene_looks_bad(scene: str) -> bool:
    text = clean_text(scene)
    if not text:
        return True
    if vision_watch_scene_is_unreliable(text):
        return True
    digit_count = len(re.findall(r"\d", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    hangul_count = len(re.findall(r"[\uac00-\ud7a3]", text))
    if re.search(r"\d{1,3}[./:]\d{1,3}[./:]\d{1,3}", text) and digit_count > max(20, latin_count + hangul_count):
        return True
    if digit_count >= 30 and latin_count + hangul_count < 12:
        return True
    return False


async def run_vision_watch_once() -> dict[str, Any]:
    frame = await asyncio.to_thread(
        capture_vision_watch_frame,
        thumbnail_size=VISION_WATCH_THUMBNAIL_SIZE,
        max_image_dim=VISION_WATCH_MAX_IMAGE_DIM,
        diff_threshold=VISION_WATCH_DIFF_THRESHOLD,
        all_screens=VISION_CAPTURE_ALL_SCREENS,
    )
    if frame.get("capture_black"):
        return frame
    now = time.time()
    changed = bool(frame.get("changed"))
    last_analyzed_at = float(frame.get("analyzed_at", 0.0) or 0.0)
    scene_bad = vision_watch_scene_looks_bad(str(frame.get("scene") or ""))
    analysis_stale = last_analyzed_at <= 0 or (now - last_analyzed_at) >= max(300.0, VISION_WATCH_ANALYZE_COOLDOWN_SEC * 4)
    if not changed and not scene_bad and not analysis_stale:
        return frame
    if (now - last_analyzed_at) < VISION_WATCH_ANALYZE_COOLDOWN_SEC and not analysis_stale:
        return frame

    last_ocr_at = float(frame.get("last_ocr_at", 0.0) or 0.0)
    run_ocr = bool(VISION_WATCH_RUN_OCR and (scene_bad or analysis_stale or (now - last_ocr_at) >= VISION_WATCH_OCR_INTERVAL_SEC))
    payload = {
        "image_path": str(frame.get("image_path") or ""),
        "prompt": build_vision_watch_prompt(),
        "run_ocr": run_ocr,
        "ocr_category": "plain",
        "max_new_tokens": 96,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=VISION_ANALYZE_TIMEOUT_SEC)
        session = await get_http_session()
        async with session.post(f"{VISION_SERVICE_URL.rstrip('/')}/v1/vision/analyze", json=payload, timeout=timeout) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(f"vision service {resp.status}: {error_text[:240]}")
            data = await resp.json()
        return update_vision_watch_analysis(data=data, run_ocr=run_ocr)
    except Exception as exc:
        return update_vision_watch_analysis(error=repr(exc), run_ocr=run_ocr)


async def vision_watch_loop() -> None:
    mark_startup_component("vision_watch", "running", "background screen observer")
    print(
        "[VISION WATCH] enabled "
        f"interval={VISION_WATCH_INTERVAL_SEC}s thumb={VISION_WATCH_THUMBNAIL_SIZE}px "
        f"analysis_max={VISION_WATCH_MAX_IMAGE_DIM}px threshold={VISION_WATCH_DIFF_THRESHOLD} "
        f"ocr={'on' if VISION_WATCH_RUN_OCR else 'off'}"
    )
    while True:
        try:
            state = await run_vision_watch_once()
            mark_startup_component(
                "vision_watch",
                "done",
                f"changed={bool(state.get('changed'))} diff={float(state.get('diff_score', 0.0) or 0.0):.3f}",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            mark_startup_component("vision_watch", "failed", repr(exc))
            print(f"[VISION WATCH] error={exc!r}")
        await asyncio.sleep(VISION_WATCH_INTERVAL_SEC)


def ensure_vision_watch_started() -> None:
    global vision_watch_task
    if not VISION_WATCH_ENABLED:
        return
    if vision_watch_task is not None and not vision_watch_task.done():
        return
    vision_watch_task = asyncio.create_task(vision_watch_loop())


def stop_vision_watch_task() -> None:
    global vision_watch_task
    task = vision_watch_task
    vision_watch_task = None
    if task is not None and not task.done():
        task.cancel()


def build_llm_context_assembly_deps() -> LlmContextAssemblyDeps:
    return LlmContextAssemblyDeps(
        compute_runtime_mode=compute_runtime_mode,
        apply_runtime_mode=apply_runtime_mode,
        classify_llm_route_fallback=classify_llm_route_fallback,
        classify_llm_route_async=classify_llm_route_async,
        session_topic_ids=session_topic_ids,
        get_conversation_history=get_conversation_history,
        read_cached_cognitive_state=read_cached_cognitive_state,
        get_matching_speculative_policy=get_matching_speculative_policy,
        fast_path_policy=fast_path_policy,
        session_state_snapshot=session_state_snapshot,
        context_policy_for_fast_path_policy=context_policy_for_fast_path_policy,
        extract_question_policy_from_route_meta=extract_question_policy_from_route_meta,
        build_fast_cognitive_state=build_fast_cognitive_state,
        update_cognitive_state=update_cognitive_state,
        schedule_cognitive_refresh=schedule_cognitive_refresh,
        build_context_policy_for_turn=build_context_policy_for_turn,
        build_tool_use_decisions=build_tool_use_decisions,
        build_runtime_status_context=build_runtime_status_context,
        clean_text=clean_text,
        build_local_tool_diagnostic_context=build_local_tool_diagnostic_context,
        project_root=PROJECT_ROOT,
        build_memory_context=build_memory_context,
        update_self_state_for_turn=update_self_state_for_turn,
        observe_live_minecraft_state=observe_live_minecraft_state,
        attach_minecraft_runtime_snapshot=attach_minecraft_runtime_snapshot,
        control_page_minecraft_cache_refresh_sec=CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC,
        control_page_minecraft_cache_max_stale_sec=CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC,
        build_conversation_state_context=build_conversation_state_context,
        build_runtime_state_context=build_runtime_state_context,
        build_evelyn_runtime_dependency_context=build_evelyn_runtime_dependency_context,
        render_self_judgment_context=render_self_judgment_context,
        render_self_state_context=render_self_state_context,
        render_vision_watch_context=render_vision_watch_context,
        build_minecraft_skill_context=build_minecraft_skill_context,
        odyssey_capability_json_dir=ODYSSEY_CAPABILITY_JSON_DIR,
        build_skill_context_hint=build_skill_context_hint,
        build_vision_context_hint=build_vision_context_hint,
        build_live_vision_context=build_live_vision_context,
        render_tool_use_context=render_tool_use_context,
        build_basic_context_packet=build_basic_context_packet,
        ask_confidence_threshold_for_source=ask_confidence_threshold_for_source,
        apply_ask_gating=apply_ask_gating,
        log_turn_event=log_turn_event,
        visible_text=visible_text,
        log=print,
    )


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
    return await prepare_llm_messages_from_runtime(
        user_text,
        deps=build_llm_context_assembly_deps(),
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
    purpose: str = "memory_summary",
    hot_path: bool = False,
    turn_id: str | None = None,
    session_key: str | None = None,
    source: str | None = None,
    guild_id: int | None = None,
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
    started_at = time.monotonic()

    async with session.post(SUMMARY_LLM_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"요약 LLM 서버 오류: {resp.status} / {error_text[:300]}")

        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            result: dict = {}
            record_model_call_trace(
                model_role="summary",
                purpose=purpose,
                hot_path=hot_path,
                started_at=started_at,
                success=True,
                model_name=SUMMARY_MODEL_NAME,
                endpoint=SUMMARY_LLM_URL,
                turn_id=turn_id,
                session_key=session_key,
                source=source,
                guild_id=guild_id,
            )
            return result

        msg = choices[0].get("message", {})
        text = clean_text(msg.get("content", "") or msg.get("reasoning_content", ""))
        result = extract_json_object(text)
        record_model_call_trace(
            model_role="summary",
            purpose=purpose,
            hot_path=hot_path,
            started_at=started_at,
            success=True,
            model_name=SUMMARY_MODEL_NAME,
            endpoint=SUMMARY_LLM_URL,
            turn_id=turn_id,
            session_key=session_key,
            source=source,
            guild_id=guild_id,
        )
        return result


async def ask_router_llm(
    messages: list[dict],
    *,
    max_tokens: int,
    timeout_seconds: float,
    purpose: str = "route",
    hot_path: bool = True,
    turn_id: str | None = None,
    session_key: str | None = None,
    source: str | None = None,
    guild_id: int | None = None,
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
    started_at = time.monotonic()

    async with session.post(ROUTER_LLM_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"router LLM 서버 오류: {resp.status} / {error_text[:300]}")

        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            result: dict = {}
            record_model_call_trace(
                model_role="router",
                purpose=purpose,
                hot_path=hot_path,
                started_at=started_at,
                success=True,
                model_name=ROUTER_MODEL_NAME,
                endpoint=ROUTER_LLM_URL,
                turn_id=turn_id,
                session_key=session_key,
                source=source,
                guild_id=guild_id,
            )
            return result

        msg = choices[0].get("message", {})
        text = clean_text(msg.get("content", "") or msg.get("reasoning_content", ""))
        result = extract_json_object(text)
        record_model_call_trace(
            model_role="router",
            purpose=purpose,
            hot_path=hot_path,
            started_at=started_at,
            success=True,
            model_name=ROUTER_MODEL_NAME,
            endpoint=ROUTER_LLM_URL,
            turn_id=turn_id,
            session_key=session_key,
            source=source,
            guild_id=guild_id,
        )
        return result


async def classify_llm_route_async(user_text: str, *, guild_id: int | None = None, source: str = "text", session_key: str | None = None) -> tuple[str, dict | None]:
    fallback_route = classify_llm_route_fallback(user_text, source=source)
    budget = build_turn_execution_budget(
        router_timeout_sec=ROUTER_ROUTE_TIMEOUT_SEC,
        context_timeout_sec=COGNITIVE_TIMEOUT_SEC,
        memory_timeout_sec=COGNITIVE_TIMEOUT_SEC,
        fallback_route=fallback_route,
        router_enabled=ROUTER_LLM_ENABLED,
    )
    fast_policy = fast_path_policy(user_text, source, session_state_snapshot(session_key))
    if fast_policy is not None:
        fast_route = normalize_route_name(str(fast_policy.get("route", fallback_route)))
        fast_budget = build_turn_execution_budget(
            router_timeout_sec=ROUTER_ROUTE_TIMEOUT_SEC,
            context_timeout_sec=COGNITIVE_TIMEOUT_SEC,
            memory_timeout_sec=COGNITIVE_TIMEOUT_SEC,
            fallback_route=fallback_route,
            router_enabled=False,
            context_policy=fast_policy,
            fallback_reason="fast_path",
        )
        return fast_route, {
            "selected": fast_route,
            "source": "fast_path",
            "confidence": 0.92,
            "reason_brief": clean_text(str(fast_policy.get("reason_brief", "fast_path"))),
            "fallback": fallback_route,
            "execution_budget": fast_budget.to_dict(),
        }
    force_voice_context = source == "voice" and should_force_voice_context_route(user_text)
    if (source == "voice" and not force_voice_context) or not ROUTER_LLM_ENABLED:
        return fallback_route, {"selected": fallback_route, "source": "fallback", "execution_budget": budget.to_dict()}

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
                '"context_focus":["current_goal"],"response_mode":"short|normal|detailed|action_only"},'
                '"ask_mode":"none|clarify|soft_followup|preference_probe|topic_continue|idle_checkin",'
                '"max_question_count":0,"question_reason":"short reason","question_hint":"direction only","question_source":"router"}. '
                "Use main_direct for ordinary direct replies, voice_context when recent state/memory is important, "
                "and sub_wait when search/wait/search_then_answer style reasoning is needed. "
                "Set minecraft/vision/skill flags only when the current turn needs them. "
                "Question rules: do not add a router call just for questions; if a direct answer/task/fix is requested, "
                "use ask_mode=none and max_question_count=0. If a light follow-up is useful, allow at most one question. "
                "question_hint is only a direction, not a final sentence."
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
        result = await ask_router_llm(
            messages,
            max_tokens=ROUTER_ROUTE_MAX_TOKENS,
            timeout_seconds=budget.router_timeout_sec,
            purpose="route",
            hot_path=True,
            turn_id=current_turn_id(session_key),
            session_key=session_key,
            source=source,
            guild_id=guild_id,
        )
    except Exception as e:
        print(f"[ROUTER] route 실패 fallback 사용: {e!r}")
        return fallback_route, {"selected": fallback_route, "source": "fallback", "error": clean_text(repr(e))[:120], "execution_budget": budget.to_dict()}

    if not isinstance(result, dict):
        return fallback_route, {"selected": fallback_route, "source": "fallback", "reason_brief": "invalid_router_json", "execution_budget": budget.to_dict()}

    selected = normalize_route_name(str(result.get("selected", fallback_route)))
    meta = {
        "selected": selected,
        "source": "router",
        "confidence": float(result.get("confidence", 0.0) or 0.0),
        "reason_brief": clean_text(str(result.get("reason_brief", ""))),
        "fallback": fallback_route,
        "execution_budget": budget.to_dict(),
    }
    question_policy = normalize_question_policy_mapping(
        {
            "ask_mode": result.get("ask_mode"),
            "max_question_count": result.get("max_question_count"),
            "question_hint": result.get("question_hint"),
            "question_reason": result.get("question_reason"),
            "question_source": result.get("question_source") or "router",
        },
        default_source="router",
    )
    meta.update(question_policy)
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
            current_summary = layered_summary_text(layers)
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
            recent = recent_memory_groups(
                layers,
                raw_limit=MEMORY_COGNITIVE_RAW_LIMIT,
                facts_limit=4,
                questions_limit=4,
            )

            messages = build_cognitive_state_messages(
                current_state=current_state,
                current_summary=current_summary,
                recent_raw=recent["raw"],
                recent_facts=recent["facts"],
                recent_questions=recent["questions"],
                user_text=user_text,
                raw_limit=MEMORY_COGNITIVE_RAW_LIMIT,
            )

            try:
                if turn_scope is not None:
                    turn_scope.raise_if_cancelled()
                result = await ask_router_llm(
                    messages,
                    max_tokens=COGNITIVE_MAX_TOKENS,
                    timeout_seconds=COGNITIVE_TIMEOUT_SEC,
                    purpose="cognitive",
                    hot_path=True,
                    turn_id=current_turn_id(session_key),
                    session_key=session_key,
                    source=source,
                    guild_id=guild_id,
                )
            except Exception as e:
                if is_context_size_error(e):
                    compact_messages = build_compact_cognitive_state_messages(
                        current_summary=current_summary,
                        user_text=user_text,
                    )
                    try:
                        if turn_scope is not None:
                            turn_scope.raise_if_cancelled()
                        result = await ask_router_llm(
                            compact_messages,
                            max_tokens=COGNITIVE_MAX_TOKENS,
                            timeout_seconds=max(3.0, COGNITIVE_TIMEOUT_SEC - 2.0),
                            purpose="cognitive",
                            hot_path=True,
                            turn_id=current_turn_id(session_key),
                            session_key=session_key,
                            source=source,
                            guild_id=guild_id,
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
                    fallback = build_cognitive_fallback_state(
                        current_state=current_state,
                        user_text=user_text,
                    )
                    write_json_file(cognitive_state_path(guild_id, scope_type=scope_type, scope_key=scope_key), fallback)
                    return fallback

            if turn_scope is not None:
                turn_scope.raise_if_cancelled()
            state = finalize_cognitive_state(
                result,
                current_state=current_state,
                user_text=user_text,
            )
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
    task = _attach_current_task(turn_scope)
    lock = memory_locks.setdefault(guild_id, asyncio.Lock())
    try:
        async with lock:
            await run_long_term_memory_update(
                guild_id,
                user_text,
                answer,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                turn_scope=turn_scope,
                collect_layers=collect_memory_layers,
                ask_summary_llm=ask_summary_llm,
                is_context_size_error=is_context_size_error,
                should_log_latency=should_log_voice_timing,
                memory_fact_limit=MEMORY_FACT_LIMIT,
                memory_loop_limit=MEMORY_LOOP_LIMIT,
                raw_limit=MEMORY_LONGTERM_RAW_LIMIT,
                log=print,
            )
    finally:
        _detach_task(turn_scope, task)
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


def redact_vision_text_for_memory(text: str) -> str:
    return redact_vision_text_for_memory_payload(
        text,
        vision_memory_write_enabled=VISION_MEMORY_WRITE_ENABLED,
    )


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
    turn_write = write_memory_turn_records(
        guild_id,
        user_text,
        answer,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        user_speaker=user_speaker,
        assistant_speaker=assistant_speaker,
        vision_memory_write_enabled=VISION_MEMORY_WRITE_ENABLED,
        record_identity_turn=record_self_identity_turn,
        append_raw_rows=append_raw_transcript_rows,
        append_vault_rows=append_turn_rows_to_memory_vault,
        log=print,
    )
    memory_user_text = turn_write.memory_user_text
    memory_answer = turn_write.memory_answer

    mode = runtime_mode or "normal"
    if mode != "realtime":
        schedule_memory_vault_maintenance(guild_id, turn_scope=turn_scope)
    refresh_inputs = memory_refresh_inputs_for_turn(
        user_text=memory_user_text,
        source=source,
        session_key=session_key,
        guild_id=guild_id,
        history_reader=get_conversation_history,
        last_active_at=session_last_active_at,
        deep_routing_needed=needs_search_or_deep_routing,
    )
    memory_writer_decision = build_memory_writer_decision_for_turn(
        user_text=memory_user_text,
        answer=memory_answer,
        source=source,
        runtime_mode=mode,
        refresh_inputs=refresh_inputs,
        decision_builder=build_memory_writer_decision,
    )
    decision_payload = build_memory_writer_decision_payload(
        memory_writer_decision,
        source=source,
        session_key=session_key,
        raw_transcript_written=True,
        vault_mirrored=turn_write.vault_mirrored,
        identity_record_decision=turn_write.identity_record_decision,
    )
    schedule_plan = plan_memory_writebehind_schedule(
        memory_writer_decision,
        mode=mode,
        guild_id=guild_id,
        session_memory_key=session_memory_key,
        room_key=room_key,
        session_key=session_key,
        decision_payload=decision_payload,
        runtime_session_key=runtime_session_key,
        task_key_builder=memory_writebehind_task_key,
        should_replace_task=should_replace_existing_memory_task,
    )
    if schedule_plan.action == "skip":
        mark_memory_writer_status(
            decision_payload,
            schedule_plan.status,
            event_path=MEMORY_WRITEBEHIND_STATUS_LOG,
            log=print,
            writebehind_reason=schedule_plan.writebehind_reason,
        )
        return decision_payload

    if schedule_plan.action == "defer":
        mark_memory_writer_status(
            decision_payload,
            schedule_plan.status,
            event_path=MEMORY_WRITEBEHIND_STATUS_LOG,
            log=print,
            writebehind_reason=schedule_plan.writebehind_reason,
        )
        return decision_payload

    if schedule_plan.action == "batch" and schedule_plan.task_key is not None:
        memory_task_key = schedule_plan.task_key
        existing = background_memory_tasks.get(memory_task_key)
        if existing is not None and not existing.done() and schedule_plan.replace_existing:
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
                            memory_user_text,
                            memory_answer,
                            room_key=room_key,
                            person_key=person_key,
                            session_memory_key=session_memory_key,
                            turn_scope=turn_scope,
                        ),
                        lambda: update_cognitive_state(
                            guild_id,
                            memory_user_text,
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
            schedule_plan.status,
            event_path=MEMORY_WRITEBEHIND_STATUS_LOG,
            log=print,
            writebehind_mode=schedule_plan.writebehind_mode,
        )
        background_memory_tasks[memory_task_key] = create_turn_scoped_task(_batched_memory_refresh(), turn_scope=turn_scope)
        return decision_payload

    async def _memory_writebehind() -> None:
        await run_memory_writebehind_steps(
            decision_payload,
            [
                lambda: update_long_term_memory(
                    guild_id,
                    memory_user_text,
                    memory_answer,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    turn_scope=turn_scope,
                ),
                lambda: update_cognitive_state(
                    guild_id,
                    memory_user_text,
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
        schedule_plan.status,
        event_path=MEMORY_WRITEBEHIND_STATUS_LOG,
        log=print,
        writebehind_mode=schedule_plan.writebehind_mode,
    )
    create_turn_scoped_task(_memory_writebehind(), turn_scope=turn_scope)
    return decision_payload


def sanitize_model_output(text: str) -> str:
    return sanitize_model_output_payload(
        text,
        stop_tokens=MAIN_LLM_STOP_TOKENS,
        cleanup_artifacts_fn=cleanup_assistant_display_artifacts,
    )


def extract_answer_from_reasoning(reasoning: str, user_text: str) -> str:
    return extract_answer_from_reasoning_payload(
        reasoning,
        user_text,
        sanitize_output_fn=sanitize_model_output,
    )


async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    if http_session is None or http_session.closed:
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10)
        http_session = aiohttp.ClientSession(timeout=timeout)
    return http_session


def build_search_query(
    guild_id: int | None,
    user_text: str,
    *,
    session_key: str | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> str:
    context_messages = list(messages or [])
    if not context_messages and session_key is not None:
        context_messages = list(get_conversation_history(session_key=session_key, guild_id=guild_id))
    summary = ""
    if guild_id is not None:
        summary = compact_working_summary(read_text_file(memory_summary_path(guild_id)))
    return build_search_query_from_context(
        user_text,
        messages=context_messages,
        memory_summary=summary,
        has_memory_scope=guild_id is not None,
    )


async def search_duckduckgo(query: str, *, limit: int = 5) -> list[dict]:
    return [result.to_dict() for result in await search_duckduckgo_payload(query, limit=limit)]


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
                    "찾은 내용만 한국어로 바로 말해라. 출처, 링크, URL, 참고자료 목록, 괄호 citation은 절대 출력하지 마라."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"사용자 질문:\n{query}\n\n"
                    + "검색 결과:\n"
                    + "\n".join(
                        f"- {clean_text(row.get('title', ''))} | {clean_text(row.get('snippet', ''))}"
                        for row in results[:5]
                    )
                ),
            },
        ], content_format=MAIN_LLM_CHAT_CONTENT_FORMAT),
        "temperature": 0.1,
        "max_tokens": 220,
        "stream": False,
        "cache_prompt": True,
        "stop": list(MAIN_LLM_STOP_TOKENS),
    }

    async with session.post(LLM_SERVER_URL, json=payload, timeout=aiohttp.ClientTimeout(total=45)) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"검색 정리 LLM 오류: {resp.status} / {error_text[:300]}")
        data = await resp.json()

    choices = data.get("choices", [])
    if not choices:
        first = results[0]
        return strip_search_answer_sources(f"찾아보니까 {first.get('snippet', '')}")

    message = choices[0].get("message", {})
    answer = sanitize_model_output(message.get("content", ""))
    if answer:
        return strip_search_answer_sources(answer)

    first = results[0]
    return strip_search_answer_sources(f"찾아보니까 {first.get('snippet', '')}")


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
            await send_discord_text(
                channel,
                format_display_text(answer, session_key=session_key),
                reference_message_id=reply_target_id,
                reference_factory=lambda message_id: discord.Object(id=message_id),
            )

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
    tagged_action, stripped_answer = parse_response_action_tag(answer)
    wants_search_by_tag = tagged_action == "search"
    wants_search_by_fallback = answer_promises_search(stripped_answer)
    if wants_search_by_tag:
        wants_search_by_fallback = False
    if opts.get("skip_search_followup") and not force and not wants_search_by_tag and not wants_search_by_fallback:
        return
    if not force and not wants_search_by_tag and not wants_search_by_fallback:
        return
    query = build_search_query(guild_id, user_text, session_key=session_key)
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
        mark_startup_component("opus", "done", "already loaded")
        print("[OPUS LOAD] already_loaded")
        return
    mark_startup_component("opus", "running", "loading Opus")
    try:
        discord_opus._load_default()
    except Exception as e:
        mark_startup_component("opus", "failed", repr(e))
        raise RuntimeError(f"Opus library load failed: {e!r}") from e
    if not discord_opus.is_loaded():
        mark_startup_component("opus", "failed", "library did not report loaded")
        raise RuntimeError("Opus library did not report loaded after default load")
    mark_startup_component("opus", "done")
    print("[OPUS LOAD] done")


def warmup_stt_sync() -> None:
    mark_startup_component("stt", "running", "STT model warmup")
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
        mark_startup_component("stt", "failed", repr(e))
        raise RuntimeError(f"STT warmup failed: {e!r}") from e
    mark_startup_component("stt", "done")
    print("[STARTUP] stt_warmup_done")


async def warmup_llm() -> None:
    mark_startup_component("main_warmup", "running", "Main LLM warmup request")
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
        "stop": list(MAIN_LLM_STOP_TOKENS),
    }
    print("[STARTUP] llm_warmup_begin")
    async with session.post(LLM_SERVER_URL, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            mark_startup_component("main_warmup", "failed", f"{resp.status}: {error_text[:160]}")
            raise RuntimeError(f"LLM warmup failed: {resp.status} / {error_text[:300]}")
        async for raw_line in resp.content:
            event = decode_sse_stream_line(raw_line)
            if not event or event.get("done"):
                continue
            if event.get("delta_text"):
                mark_startup_component("main_warmup", "done")
                print("[STARTUP] llm_warmup_done")
                return
    mark_startup_component("main_warmup", "done", "no streamed chunk")
    print("[STARTUP] llm_warmup_done_no_chunk")


async def warmup_voice_path(*, reason: str, key: str | None = None, include_stt: bool = True, include_llm: bool = True, include_tts: bool = True) -> None:
    lock_key = key or reason
    lock = voice_path_warmup_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        if key is not None and voice_path_warmup_done.get(key):
            return
        print(f"[STARTUP] voice_path_warmup_begin reason={reason} key={lock_key}")
        if include_stt:
            if not STT_SERVICE_URL:
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
    input_mode = normalize_voice_input_mode(str(local_mic_runtime_state.get("input_mode") or "auto"))
    local_mic_runtime_state["input_mode"] = input_mode
    capture_ready = bool(local_mic_service and local_mic_service.capture_ready)
    local_mic_runtime_state["capture_ready"] = capture_ready
    local_mic_recent = False
    last_segment_at = local_mic_runtime_state.get("last_segment_at")
    if isinstance(last_segment_at, (int, float)):
        local_mic_recent = (time.time() - float(last_segment_at)) <= LOCAL_MIC_DISCORD_SUPPRESS_AFTER_SEGMENT_SEC
    decision = decide_local_mic_discord_suppression(
        LocalMicDiscordSuppressionInput(
            member_id=member_id,
            source=source,
            input_mode=input_mode,
            capture_ready=capture_ready,
            local_mic_recent=local_mic_recent,
            preferred_user_ids=LOCAL_MIC_DISCORD_USER_IDS,
        ),
        normalize_voice_input_mode=normalize_voice_input_mode,
        should_route_discord_user_to_local_mic=should_route_discord_user_to_local_mic,
    )
    local_mic_runtime_state["input_mode"] = decision.normalized_input_mode
    local_mic_runtime_state["discord_suppression_active"] = decision.suppress
    return decision.suppress


def set_voice_input_mode(mode: str | None) -> str:
    return set_voice_input_mode_state(local_mic_runtime_state, mode)


def voice_input_mode_status_line() -> str:
    return voice_input_mode_status_line_from_mode(str(local_mic_runtime_state.get("input_mode") or "auto"))


def serialize_local_mic_runtime_state() -> dict[str, Any]:
    return serialize_local_mic_runtime_state_payload(
        local_mic_runtime_state,
        service=local_mic_service,
        max_silence_ms=LOCAL_MIC_MAX_SILENCE_MS,
        vad_filter_enabled=LOCAL_MIC_VAD_FILTER_ENABLED,
        env_noise_filter_enabled=LOCAL_MIC_ENV_NOISE_FILTER_ENABLED,
        waveform_filter_enabled=LOCAL_MIC_WAVEFORM_FILTER_ENABLED,
        discord_suppress_after_segment_sec=LOCAL_MIC_DISCORD_SUPPRESS_AFTER_SEGMENT_SEC,
        device=LOCAL_MIC_DEVICE,
        sample_rate=LOCAL_MIC_SAMPLE_RATE,
        start_threshold=LOCAL_MIC_START_THRESHOLD,
        continue_threshold=LOCAL_MIC_CONTINUE_THRESHOLD,
    )


def local_mic_status_line() -> str:
    return local_mic_status_line_from_payload(serialize_local_mic_runtime_state())


def build_evelyn_runtime_dependency_context() -> str:
    local_tts = local_tts_playback_manager.snapshot()
    local_mic = serialize_local_mic_runtime_state()
    output_mode = "local_speaker" if LOCAL_ONLY_MODE and local_tts_playback_manager.enabled else (
        "discord_voice" if DISCORD_ENABLED else "none"
    )
    lines = [
        "Evelyn dependency topology:",
        f"- self_runtime: main.py control/runtime process; local_only={LOCAL_ONLY_MODE}; discord_enabled={DISCORD_ENABLED}.",
        f"- main_llm: {MODEL_NAME}; endpoint={LLM_SERVER_URL}; role=primary answer text generation.",
        f"- router_llm: {ROUTER_MODEL_NAME}; role=route/cognitive policy before the main answer.",
        f"- summary_llm: {SUMMARY_MODEL_NAME}; role=memory summaries/background consolidation.",
        f"- stt: {STT_MODEL_NAME}; backend={STT_BACKEND}; role=voice input -> transcript before main_llm.",
        f"- tts: OmniVoice endpoint={OMNIVOICE_SERVER_URL}; voice={OMNIVOICE_VOICE or 'auto'}; speed={OMNIVOICE_SPEED}; role=text -> spoken audio after main_llm.",
        f"- voice_io: input_mode={voice_input_mode_status_line()}; output_mode={output_mode}.",
        (
            "- local_mic: "
            f"enabled={local_mic.get('enabled')} capture_ready={local_mic.get('captureReady')} "
            f"device={local_mic.get('device')} segments={local_mic.get('segmentCount')} "
            f"last_error={local_mic.get('lastError') or 'none'}."
        ),
        (
            "- local_tts_output: "
            f"enabled={local_tts.get('enabled')} active={local_tts.get('active')} "
            f"device={local_tts.get('device') or 'default'} play_count={local_tts.get('playCount')} "
            f"last_error={local_tts.get('lastError') or 'none'}."
        ),
        "- minecraft_voyager: optional downstream game/autonomy service; use live runtime status before claiming current game state.",
        "- codex_gateway: optional external coding/control helper; if status says standby/not ready, say so instead of assuming it is available.",
        "Rule: when the user asks about your own state, dependencies, voice path, or local/Discord mode, answer from this runtime topology and status, not from persona guesses.",
    ]
    return "\n".join(lines)


async def handle_local_mic_segment(pcm_bytes: bytes, debug_meta: dict[str, Any] | None = None) -> None:
    if not pcm_bytes:
        return
    if normalize_voice_input_mode(str(local_mic_runtime_state.get("input_mode") or "auto")) == "discord":
        return
    local_mic_runtime_state["segment_count"] = int(local_mic_runtime_state.get("segment_count") or 0) + 1
    local_mic_runtime_state["last_segment_at"] = time.time()
    if isinstance(debug_meta, dict):
        local_mic_runtime_state["last_segment_duration_sec"] = debug_meta.get("duration_sec")
        local_mic_runtime_state["last_filter"] = debug_meta.get("voice_filter")
    target = resolve_local_mic_target(guilds=bot.guilds, preferred_user_ids=LOCAL_MIC_DISCORD_USER_IDS)
    routed_meta = dict(debug_meta or {})
    routed_meta["source"] = "local_mic"
    if target is None and LOCAL_ONLY_MODE:
        member = local_control_voice_member()
        local_mic_runtime_state["last_error"] = None
        routed_meta["routed_local_control"] = True
        routed_meta["routed_discord_user_id"] = int(getattr(member, "id", 0) or 0)
        print(
            f"[LOCAL MIC] segment routed=local_control user_id={member.id} "
            f"duration={routed_meta.get('duration_sec')}"
        )
        await process_member_audio(member, pcm_bytes, routed_meta)
        return
    if target is None:
        local_mic_runtime_state["last_error"] = "no_active_discord_target_for_local_mic"
        return
    local_mic_runtime_state["last_error"] = None
    routed_meta["routed_discord_user_id"] = int(getattr(target.member, "id", 0) or 0)
    await process_member_audio(target.member, pcm_bytes, routed_meta)


def local_mic_effective_max_silence_ms() -> int:
    if local_tts_playback_manager.snapshot().get("active"):
        return LOCAL_MIC_TTS_ACTIVE_MAX_SILENCE_MS
    return LOCAL_MIC_MAX_SILENCE_MS


async def ensure_local_mic_service_started() -> None:
    global local_mic_service
    if not LOCAL_MIC_ENABLED:
        local_mic_runtime_state["capture_ready"] = False
        return
    if not LOCAL_ONLY_MODE and not LOCAL_MIC_DISCORD_USER_IDS:
        local_mic_runtime_state["capture_ready"] = False
        local_mic_runtime_state["last_error"] = "no_local_mic_user_ids"
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
        max_silence_ms_provider=local_mic_effective_max_silence_ms,
        preroll_ms=LOCAL_MIC_PREROLL_MS,
        max_segment_sec=LOCAL_MIC_MAX_SEGMENT_SEC,
        device=LOCAL_MIC_DEVICE,
        queue_max=LOCAL_MIC_QUEUE_MAX,
        vad_filter_enabled=LOCAL_MIC_VAD_FILTER_ENABLED,
        env_noise_filter_enabled=LOCAL_MIC_ENV_NOISE_FILTER_ENABLED,
        waveform_filter_enabled=LOCAL_MIC_WAVEFORM_FILTER_ENABLED,
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
    mark_startup_component("tts_warmup", "running", "OmniVoice health check")

    session = await get_http_session()
    timeout = aiohttp.ClientTimeout(total=10)
    async with session.get(f"{OMNIVOICE_SERVER_URL}/health", timeout=timeout) as resp:
        if resp.status != 200:
            text = await resp.text()
            mark_startup_component("tts_warmup", "failed", f"health {resp.status}: {text[:160]}")
            raise RuntimeError(f"OmniVoice health check 실패: {resp.status} / {text[:200]}")
        print("OmniVoice 서버 준비 확인 완료")

    if os.getenv("TTS_WARMUP_GENERATE_ENABLED", "false").lower() not in {"1", "true", "yes", "on"}:
        mark_startup_component("tts_warmup", "done", "health check only")
        return

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
            mark_startup_component("tts_warmup", "failed", f"warmup {resp.status}: {text[:160]}")
            raise RuntimeError(f"OmniVoice warmup 실패: {resp.status} / {text[:200]}")
        async for chunk in resp.content.iter_chunked(4096):
            if chunk:
                mark_startup_component("tts_warmup", "done")
                print("OmniVoice TTS 워밍업 완료")
                break
        if not startup_component_done("tts_warmup"):
            mark_startup_component("tts_warmup", "done", "no audio chunk returned")


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
        "local_first_playback_logged": ["t_local_first_playback"],
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
            f"local_playback={_fmt('local_first_playback_logged')}",
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
                "num_step": OMNIVOICE_NUM_STEP,
            }
            if OMNIVOICE_SPEED > 0 and abs(OMNIVOICE_SPEED - 1.0) > 0.001:
                payload["speed"] = OMNIVOICE_SPEED
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
                if not first_pcm_logged:
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
                        error_code="empty_audio",
                        error_text="OmniVoice returned no PCM bytes.",
                        metadata=tts_request.metadata,
                    )
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
        detail = f" ({type(QWEN_ASR_IMPORT_ERROR).__name__}: {QWEN_ASR_IMPORT_ERROR})" if QWEN_ASR_IMPORT_ERROR else ""
        raise RuntimeError(f"qwen-asr를 불러오지 못했습니다{detail}. STT 의존성을 확인한 뒤 다시 실행하세요.")

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
    if STT_SERVICE_URL:
        try:
            language = normalize_stt_language() if STT_FORCE_LANGUAGE else None
            result = transcribe_audio16k_via_service(
                audio16k,
                service_url=STT_SERVICE_URL,
                timeout_sec=STT_SERVICE_TIMEOUT_SEC,
                sampling_rate=effective_rate,
                max_new_tokens=max_new_tokens,
                stage=stage,
                language=language,
            )
            text = clean_text(str(result.get("text") or ""))
            print(f"[STT REMOTE DONE][{stage}] text={text!r}")
            return text
        except Exception as exc:
            print(f"[STT REMOTE FAIL][{stage}] {exc!r}")
            if not STT_SERVICE_FALLBACK_LOCAL:
                raise

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


async def stop_active_tts_playback(guild_id: int | None, *, reason: str = "interrupt") -> bool:
    stopped = await tts_playback_manager.cancel_guild(guild_id)
    if not stopped:
        return False
    log_turn_event("tts_interrupt", guild_id=guild_id, reason=reason)
    return True


async def verify_speaker_for_tts_interrupt(
    audio: np.ndarray,
    *,
    sampling_rate: int,
    source: str | None,
    metrics: dict | None = None,
) -> SpeakerVerificationResult:
    if not speaker_verification_applies(source=source, apply_to=SPEAKER_VERIFICATION_APPLY_TO):
        result = SpeakerVerificationResult("skipped", threshold=SPEAKER_VERIFICATION_THRESHOLD, detail=f"source={source or ''}")
    else:
        result = await asyncio.to_thread(speaker_verifier.verify, audio, sampling_rate=sampling_rate)
    if metrics is not None:
        metrics.setdefault("meta", {})["speaker_verification"] = result.to_dict()
    return result


def speaker_verification_allows_tts_interrupt(result: SpeakerVerificationResult) -> bool:
    return result.matched is not False


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
    log_turn_event(
        "cached_audio_playback_selected",
        turn_id=turn_id,
        session_key=session_key,
        path=str(path),
        answer=clean_text(answer),
    )
    await tts_playback_manager.play_source_once(
        TtsSourcePlaybackRequest(
            vc,
            source,
            guild_id=guild_id,
            turn_id=turn_id,
            session_key=session_key,
            metrics=metrics,
            trace_payload={
                "cached_audio_path": str(path),
            },
            cleanup_source=True,
        )
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
    if is_local_speaker_voice_client(vc):
        await speak_answer_local(
            answer,
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
            metrics=metrics,
        )
        return

    guild_id = getattr(getattr(vc, "guild", None), "id", None)
    if turn_scope is not None:
        turn_scope.transition(TurnState.TTS_RUNNING, reason="speak_answer")

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
        await tts_playback_manager.play_source_once(
            TtsSourcePlaybackRequest(
                vc,
                source,
                guild_id=guild_id,
                turn_id=turn_id,
                session_key=session_key,
                metrics=metrics,
                trace_payload={
                },
                clear_registry_on_finish=False,
            )
        )


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
    task = _attach_current_task(turn_scope)
    if turn_scope is not None:
        turn_scope.transition(TurnState.TTS_RUNNING, reason="stream_tts_sentences")

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
                log_voice_latency(metrics, "tts_request_logged", "TTS request start")
            ),
            on_response_headers=lambda: log_voice_latency(metrics, "tts_response_headers_logged", "TTS response headers"),
            on_first_byte=lambda: (
                mark_turn_stage(metrics, "tts_first_byte", event_name="tts_first_byte", chunk_index=chunk_index),
                log_voice_latency(metrics, "tts_first_byte_logged", "TTS first byte")
            ),
            on_first_frame=lambda: log_voice_latency(metrics, "tts_first_frame_logged", "TTS first frame"),
            on_first_packet_sent=lambda ci=chunk_index: (
                log_voice_latency(metrics, "first_packet_sent_logged", "first packet sent"),
                log_turn_event(
                    "first_packet_sent",
                    turn_id=turn_id,
                    chunk_index=ci,
                    session_key=session_key,
                )
            ),
        )

    def record_playback_failure(exc: Exception, *, stage: str) -> None:
        record_voice_pipeline_failure(
            "tts_playback_failed",
            exc,
            metrics,
            turn_id=turn_id,
            session_key=session_key,
            stage=stage,
        )

    try:
        async with tts_lock:
            await tts_playback_manager.stream_sentences(
                TtsStreamingPlaybackRequest(
                    vc=vc,
                    sentence_queue=sentence_queue,
                    synthesize_source=synthesize_source,
                    guild_id=guild_id,
                    turn_id=turn_id,
                    session_key=session_key,
                    metrics=metrics,
                    ready_timeout_sec=OMNIVOICE_TIMEOUT_SEC,
                    prefetch_chunks=TTS_PREFETCH_CHUNKS,
                    lookahead_chunks=TTS_PLAYBACK_START_LOOKAHEAD_CHUNKS,
                    lookahead_timeout_ms=TTS_PLAYBACK_START_LOOKAHEAD_TIMEOUT_MS,
                    create_task=lambda coro: create_turn_scoped_task(coro, turn_scope=turn_scope),
                    check_cancelled=check_cancelled,
                    log=print,
                    on_prefetch_failure=lambda exc: record_playback_failure(exc, stage="prefetch"),
                    on_prepared_failure=lambda exc: record_playback_failure(exc, stage="prepared_exception"),
                )
            )
    finally:
        _detach_task(turn_scope, task)


async def speak_answer_local(
    answer: str,
    *,
    turn_id: str | None = None,
    session_key: str | None = None,
    turn_scope: TurnScope | None = None,
    metrics: dict | None = None,
) -> bool:
    if not local_tts_playback_manager.enabled:
        return False
    text = clean_tts_text(strip_omnivoice_tags(answer) or answer)
    if not text:
        return False
    task = _attach_current_task(turn_scope)
    if turn_scope is not None:
        turn_scope.transition(TurnState.TTS_RUNNING, reason="local_speaker_tts")
    try:
        async with tts_lock:
            source = await create_omnivoice_source(
                text,
                turn_id=turn_id,
                chunk_index=1,
                session_key=session_key,
                turn_scope=turn_scope,
                trace_payload={"source_type": "LocalSpeakerOmniVoicePCMStream", "output_mode": "local_speaker"},
                on_request_start=lambda: (
                    mark_turn_stage(metrics, "tts_request_start", event_name="local_tts_request_start", chunk_index=1),
                    log_voice_latency(metrics, "tts_request_logged", "Local TTS request start"),
                ),
                on_response_headers=lambda: log_voice_latency(metrics, "tts_response_headers_logged", "Local TTS response headers"),
                on_first_byte=lambda: (
                    mark_turn_stage(metrics, "tts_first_byte", event_name="local_tts_first_byte", chunk_index=1),
                    log_voice_latency(metrics, "tts_first_byte_logged", "Local TTS first byte"),
                ),
                on_first_frame=lambda: log_voice_latency(metrics, "tts_first_frame_logged", "Local TTS first frame"),
                on_first_packet_sent=lambda: (
                    log_voice_latency(metrics, "first_packet_sent_logged", "Local speaker first packet"),
                    log_turn_event(
                        "local_tts_first_packet_sent",
                        turn_id=turn_id,
                        chunk_index=1,
                        session_key=session_key,
                    ),
                ),
            )
            wait_until_ready = getattr(source, "wait_until_ready", None)
            if wait_until_ready is not None:
                await wait_until_ready(timeout=max(0.2, OMNIVOICE_TIMEOUT_SEC))
            return await local_tts_playback_manager.play_source(
                source,
                cleanup_source=True,
                on_first_playback=lambda: _mark_local_tts_first_playback(
                    metrics,
                    turn_id=turn_id,
                    chunk_index=1,
                    session_key=session_key,
                ),
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        record_voice_pipeline_failure("tts_playback_failed", exc, metrics, turn_id=turn_id, session_key=session_key, stage="local_speaker")
        return False
    finally:
        _detach_task(turn_scope, task)


def _cleanup_prepared_tts_item(item: object) -> None:
    if isinstance(item, tuple) and len(item) >= 2:
        cleanup = getattr(item[1], "cleanup", None)
        if cleanup is not None:
            with contextlib.suppress(Exception):
                cleanup()


def _mark_local_tts_first_playback(
    metrics: dict | None,
    *,
    turn_id: str | None,
    chunk_index: int,
    session_key: str | None,
) -> None:
    if not metrics or "local_tts_first_playback" not in (metrics.get("marks") or {}):
        mark_turn_stage(
            metrics,
            "local_tts_first_playback",
            event_name="local_tts_first_playback",
            turn_id=turn_id,
            chunk_index=chunk_index,
            session_key=session_key,
            output_mode="local_speaker",
        )
    log_voice_latency(metrics, "local_first_playback_logged", "Local speaker first playback")


async def stream_local_tts_sentences(
    sentence_queue: "asyncio.Queue[str | None]",
    *,
    metrics: dict | None = None,
    turn_id: str | None = None,
    session_key: str | None = None,
    turn_scope: TurnScope | None = None,
) -> int:
    if not local_tts_playback_manager.enabled:
        return 0

    task = _attach_current_task(turn_scope)
    if turn_scope is not None:
        turn_scope.transition(TurnState.TTS_RUNNING, reason="local_speaker_stream_tts")

    def check_cancelled() -> None:
        if turn_scope is not None:
            turn_scope.raise_if_cancelled()

    async def synthesize_source(sentence: str, chunk_index: int) -> OmniVoicePCMStream:
        text = clean_tts_text(strip_omnivoice_tags(sentence) or sentence)
        return await create_omnivoice_source(
            text,
            turn_id=turn_id,
            chunk_index=chunk_index,
            session_key=session_key,
            turn_scope=turn_scope,
            trace_payload={
                "source_type": "LocalSpeakerOmniVoicePCMStream",
                "output_mode": "local_speaker",
                "delivery_mode": "llm_sentence_stream",
            },
            on_request_start=lambda ci=chunk_index: (
                mark_turn_stage(metrics, "tts_request_start", event_name="local_tts_request_start", chunk_index=ci),
                log_voice_latency(metrics, "tts_request_logged", "Local TTS request start"),
            ),
            on_response_headers=lambda: log_voice_latency(metrics, "tts_response_headers_logged", "Local TTS response headers"),
            on_first_byte=lambda ci=chunk_index: (
                mark_turn_stage(metrics, "tts_first_byte", event_name="local_tts_first_byte", chunk_index=ci),
                log_voice_latency(metrics, "tts_first_byte_logged", "Local TTS first byte"),
            ),
            on_first_frame=lambda: log_voice_latency(metrics, "tts_first_frame_logged", "Local TTS first frame"),
            on_first_packet_sent=lambda ci=chunk_index: (
                log_voice_latency(metrics, "first_packet_sent_logged", "Local speaker first packet"),
                log_turn_event(
                    "local_tts_first_packet_sent",
                    turn_id=turn_id,
                    chunk_index=ci,
                    session_key=session_key,
                ),
            ),
        )

    def record_prefetch_failure(exc: Exception) -> None:
        record_voice_pipeline_failure(
            "tts_request_failed",
            exc,
            metrics,
            turn_id=turn_id,
            session_key=session_key,
            stage="local_speaker_stream_prefetch",
        )

    played_chunks = 0
    prepared_queue: asyncio.Queue[object] | None = None
    prefetch_task: asyncio.Task | None = None
    try:
        async with tts_lock:
            prepared_queue = asyncio.Queue(maxsize=max(1, int(TTS_PREFETCH_CHUNKS)))
            prefetch_task = create_turn_scoped_task(
                prefetch_tts_sources(
                    sentence_queue,
                    prepared_queue,
                    synthesize_source=synthesize_source,
                    ready_timeout_sec=OMNIVOICE_TIMEOUT_SEC,
                    check_cancelled=check_cancelled,
                    on_failure=record_prefetch_failure,
                ),
                turn_scope=turn_scope,
            )
            while True:
                check_cancelled()
                item = await prepared_queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                chunk_index, source = item
                try:
                    ok = await local_tts_playback_manager.play_source(
                        source,
                        cleanup_source=True,
                        on_first_playback=lambda ci=int(chunk_index or 0): _mark_local_tts_first_playback(
                            metrics,
                            turn_id=turn_id,
                            chunk_index=ci,
                            session_key=session_key,
                        ),
                    )
                except Exception as exc:
                    record_voice_pipeline_failure(
                        "tts_playback_failed",
                        exc,
                        metrics,
                        turn_id=turn_id,
                        session_key=session_key,
                        stage="local_speaker_stream",
                        chunk_index=int(chunk_index or 0),
                    )
                    raise
                if ok:
                    played_chunks += 1
    finally:
        if prefetch_task is not None and not prefetch_task.done():
            prefetch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await prefetch_task
        if prepared_queue is not None:
            while True:
                try:
                    leftover = prepared_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                _cleanup_prepared_tts_item(leftover)
        _detach_task(turn_scope, task)

    return played_chunks


def start_streaming_local_voice_delivery(
    *,
    metrics: dict,
    turn_id: str | None,
    session_key: str | None,
    turn_scope: TurnScope | None,
) -> StreamingVoiceDelivery:
    sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
    tts_sink = TTSQueueSink(sentence_queue, log=print)
    playback_task = create_turn_scoped_task(
        stream_local_tts_sentences(
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


def schedule_local_control_tts(
    answer: str,
    *,
    turn_id: str | None = None,
    session_key: str | None = None,
    turn_scope: TurnScope | None = None,
) -> asyncio.Task | None:
    if not LOCAL_ONLY_MODE or not local_tts_playback_manager.enabled:
        return None
    metrics: dict[str, Any] = {
        "started_at": time.monotonic(),
        "meta": {
            "turn_id": turn_id,
            "source": "control_page",
            "session_key": session_key,
            "turn_type": "control_page_local_tts",
            "selected_path": "local_speaker",
            "needs_tts": True,
        },
        "marks": {},
    }

    async def _runner() -> None:
        ok = False
        try:
            ok = await speak_answer_local(
                answer,
                turn_id=turn_id,
                session_key=session_key,
                turn_scope=turn_scope,
                metrics=metrics,
            )
        finally:
            log_voice_bottleneck_summary(
                metrics,
                label="local_tts",
                extra=f"control_page=true playback={'ok' if ok else 'skipped_or_failed'}",
                event_name="local_tts_summary",
            )

    return create_turn_scoped_task(_runner(), turn_scope=turn_scope)


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
    if route_decision.user_visible_preface and not is_user_echo_answer(user_text, route_decision.user_visible_preface):
        return build_answer_payload_from_text(route_decision.user_visible_preface), "", gated_state

    guided_user_text = user_text
    lightweight_persona_turn = is_casual_call_or_status_question(guided_user_text)
    live_minecraft_state = None if lightweight_persona_turn else await observe_live_minecraft_state(guild_id)
    runtime_status_context = await build_runtime_status_context(force=bool(route_decision.needs_runtime_state))
    final_user_text = f"{guided_user_text}\n\n{build_main_response_guidance(cognitive_state, source=source, user_text=guided_user_text, session_key=session_key, guild_id=guild_id, minecraft_state=live_minecraft_state, runtime_status_context=runtime_status_context, route_decision=route_decision)}"

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
        "stop": list(MAIN_LLM_STOP_TOKENS),
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

        answer = sanitize_unrequested_minecraft_leak(guided_user_text, answer)
        answer, question_shape_meta = enforce_question_limits(answer, route_decision)
        record_question_trace(
            route_decision=route_decision,
            answer=answer,
            shape_meta=question_shape_meta,
            metrics=metrics,
            cooldown_hit=bool((metrics or {}).get("meta", {}).get("question_cooldown_hit")) if isinstance(metrics, dict) else False,
        )
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
        "stop": list(MAIN_LLM_STOP_TOKENS),
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


def render_tool_synthesis_recent_context(
    messages: list[dict[str, Any]] | None,
    *,
    user_text: str,
    max_items: int = 6,
    max_chars: int = 900,
) -> str:
    current = clean_text(user_text).lower()
    rendered: list[str] = []
    for item in list(messages or [])[-max_items:]:
        if not isinstance(item, dict):
            continue
        role = clean_text(str(item.get("role") or ""))
        if role not in {"user", "assistant"}:
            continue
        content = clean_text(str(item.get("content") or ""))
        if not content or content.lower() == current:
            continue
        label = "user" if role == "user" else "assistant"
        rendered.append(f"{label}: {compact_memory_text(content, max_chars=180)}")
    context = "\n".join(rendered)
    return compact_memory_text(context, max_chars=max_chars)


def tool_synthesis_answer_drifted(answer: str, *, user_text: str, tool_result_text: str) -> bool:
    cleaned_answer = clean_text(answer)
    if not cleaned_answer:
        return False
    anchor = f"{clean_text(user_text)}\n{clean_text(tool_result_text)}"
    suspicious_terms = ("동물", "버튼", "좌표", "클릭")
    if any(term in cleaned_answer and term not in anchor for term in suspicious_terms):
        return True
    if any(phrase in cleaned_answer for phrase in ("질문했을 때", "요청했습니다", "요청했어")):
        if "날씨" in anchor and "날씨" in cleaned_answer:
            return True
    return False


async def synthesize_tool_result_with_main_llm(
    *,
    user_text: str,
    tool_name: str,
    tool_result_text: str,
    guild_id: int | None = None,
    session_key: str | None = None,
    source: str = "text",
    messages: list[dict[str, Any]] | None = None,
    cognitive_state: dict | None = None,
    route_decision: RouteDecision | None = None,
    metrics: dict | None = None,
) -> str:
    cleaned_user = clean_text(user_text)
    cleaned_result = clean_text(tool_result_text)
    if not cleaned_user or not cleaned_result:
        return cleaned_result
    if metrics is not None:
        metrics.setdefault("meta", {})["main_synthesis_requested"] = {
            "tool_name": clean_text(tool_name) or "tool",
            "tool_result_chars": len(cleaned_result),
        }
    recent_context = render_tool_synthesis_recent_context(messages, user_text=cleaned_user)
    synthesis_prompt = (
        "A tool result is now available. Produce the final answer to the user in Korean.\n"
        "This is the final answer phase, not a preface. Do not say that you will look it up now.\n"
        "Use Evelyn's normal conversational tone. If the tool result is weak or incomplete, say so plainly and give the best next step.\n"
        "Treat recent context only as a way to resolve short follow-ups like 'search it' or 'tell me the weather'.\n"
        "Do not introduce unrelated objects, buttons, coordinates, animals, or old topics unless they appear in the original request or tool result.\n"
        "Ground the final answer in the tool result below.\n\n"
        f"Original user request:\n{cleaned_user}\n\n"
        f"Recent conversation context for ellipsis resolution only:\n{recent_context or '(none)'}\n\n"
        f"Tool name:\n{clean_text(tool_name) or 'tool'}\n\n"
        f"Tool result:\n{cleaned_result}"
    )
    final_user_text = (
        f"{synthesis_prompt}\n\n"
        f"{build_main_response_guidance(cognitive_state, source=source, user_text=cleaned_user, session_key=session_key, guild_id=guild_id, route_decision=route_decision)}"
    )
    payload = build_main_llm_payload(
        model_name=MODEL_NAME,
        messages=[],
        final_user_text=final_user_text,
        source=source,
        stream=False,
        content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        max_tokens=VOICE_LLM_MAX_TOKENS,
        stop_tokens=MAIN_LLM_STOP_TOKENS,
    )
    answer, answer_source = await execute_main_llm_once(
        payload=payload,
        user_text=cleaned_user,
    )
    answer = strip_search_answer_sources(sanitize_model_output(answer))
    if tool_synthesis_answer_drifted(answer, user_text=cleaned_user, tool_result_text=cleaned_result):
        if metrics is not None:
            metrics.setdefault("meta", {})["main_synthesis_drift_guard"] = True
        answer = cleaned_result
    if route_decision is not None:
        answer, question_shape_meta = enforce_question_limits(answer, route_decision)
        record_question_trace(
            route_decision=route_decision,
            answer=answer,
            shape_meta=question_shape_meta,
            metrics=metrics,
            cooldown_hit=bool((metrics or {}).get("meta", {}).get("question_cooldown_hit")) if isinstance(metrics, dict) else False,
        )
    if metrics is not None:
        metrics.setdefault("meta", {})["main_synthesis_answer_source"] = answer_source
    return clean_text(answer) or cleaned_result


async def resolve_promised_search_final_answer(
    *,
    user_text: str,
    answer_text: str,
    guild_id: int | None = None,
    session_key: str | None = None,
    source: str = "text",
    messages: list[dict[str, Any]] | None = None,
    cognitive_state: dict | None = None,
    route_decision: RouteDecision | None = None,
    metrics: dict | None = None,
) -> str:
    answer = clean_text(answer_text)
    if not answer or not answer_promises_search(answer):
        return answer
    if has_negated_search_marker(user_text):
        if metrics is not None:
            metrics.setdefault("meta", {})["promised_search_escalation_skipped"] = "negated_search"
        return answer
    if metrics is not None:
        metrics.setdefault("meta", {})["promised_search_escalated"] = True

    action_result = await execute_search_then_answer_action(
        guild_id=guild_id,
        user_text=user_text,
        session_key=session_key,
        messages=messages,
    )
    final_answer = await synthesize_tool_result_with_main_llm(
        user_text=user_text,
        tool_name="search",
        tool_result_text=action_result.answer_text,
        guild_id=guild_id,
        session_key=session_key,
        source=source,
        messages=messages,
        cognitive_state=cognitive_state,
        route_decision=route_decision,
        metrics=metrics,
    )
    if final_answer and not answer_promises_search(final_answer):
        return final_answer
    return clean_text(action_result.answer_text) or answer


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
    record_question_trace_enabled: bool = True,
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
    if skill_route_answer and not is_user_echo_answer(user_text, skill_route_answer):
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

    if route_decision.user_visible_preface and not is_user_echo_answer(user_text, route_decision.user_visible_preface):
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
    runtime_status_context = await build_runtime_status_context(force=bool(route_decision.needs_runtime_state))
    final_user_text = f"{guided_user_text}\n\n{build_main_response_guidance(cognitive_state, source=source, user_text=guided_user_text, session_key=session_key, guild_id=guild_id, minecraft_state=live_minecraft_state, runtime_status_context=runtime_status_context, route_decision=route_decision)}"
    payload = build_main_llm_payload(
        model_name=MODEL_NAME,
        messages=messages,
        final_user_text=final_user_text,
        source=source,
        stream=False,
        content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        max_tokens=VOICE_LLM_MAX_TOKENS,
        stop_tokens=MAIN_LLM_STOP_TOKENS,
    )
    answer, answer_source = await execute_main_llm_once(
        payload=payload,
        user_text=user_text,
    )
    answer = sanitize_unrequested_minecraft_leak(guided_user_text, answer)
    answer = await resolve_promised_search_final_answer(
        user_text=user_text,
        answer_text=answer,
        guild_id=guild_id,
        session_key=session_key,
        source=source,
        messages=messages,
        cognitive_state=cognitive_state,
        route_decision=route_decision,
        metrics=metrics,
    )
    answer, question_shape_meta = enforce_question_limits(answer, route_decision)
    if record_question_trace_enabled:
        record_question_trace(
            route_decision=route_decision,
            answer=answer,
            shape_meta=question_shape_meta,
            metrics=metrics,
            cooldown_hit=bool((metrics or {}).get("meta", {}).get("question_cooldown_hit")) if isinstance(metrics, dict) else False,
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
    question_stream_state: dict[str, int] | None = None,
) -> bool:
    emitted_any = False
    if on_sentence is not None:
        for chunk in speech_chunker.push(delta_text, max_chunks=1):
            if not chunk:
                continue
            if question_stream_state is not None:
                chunk, question_meta = filter_stream_chunk_for_question_limits(
                    chunk,
                    max_question_count=int(question_stream_state.get("max_question_count", 0)),
                    question_count_so_far=int(question_stream_state.get("question_count", 0)),
                )
                question_stream_state["question_count"] = int(question_stream_state.get("question_count", 0)) + int(
                    question_meta.get("question_count_after", 0) or 0
                )
                question_stream_state["question_removed_count"] = int(question_stream_state.get("question_removed_count", 0)) + (
                    1 if question_meta.get("question_removed") else 0
                )
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
    question_stream_state: dict[str, int] | None = None,
) -> None:
    if on_sentence is None:
        return
    ready_chunks = speech_chunker.flush()
    if not ready_chunks and answer and not emitted_any:
        ready_chunks = [clean_tts_text(answer)]
    for chunk in ready_chunks:
        if not chunk:
            continue
        if question_stream_state is not None:
            chunk, question_meta = filter_stream_chunk_for_question_limits(
                chunk,
                max_question_count=int(question_stream_state.get("max_question_count", 0)),
                question_count_so_far=int(question_stream_state.get("question_count", 0)),
            )
            question_stream_state["question_count"] = int(question_stream_state.get("question_count", 0)) + int(
                question_meta.get("question_count_after", 0) or 0
            )
            question_stream_state["question_removed_count"] = int(question_stream_state.get("question_removed_count", 0)) + (
                1 if question_meta.get("question_removed") else 0
            )
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
        merged = merge_voyager_status_into_state(status, observed)
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
    merged = merge_voyager_status_into_state(None, observed) if (observed.get("connected") or observed.get("active") or observed.get("position")) else None
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
            last_observed = merge_voyager_status_into_state(status, observed) or dict(observed)
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
    merged = merge_voyager_status_into_state(started if isinstance(started, dict) else None, observed if isinstance(observed, dict) else None) or {}
    merged["voyager_repo_present"] = started.get("voyager_repo_present") if isinstance(started, dict) else None
    return merged


async def disable_minecraft_mode(guild_id: int) -> None:
    _ = guild_id
    client = get_minecraft_client()
    await client.stop()


def enqueue_control_page_ui_command(action: str, *, panel_id: str | None = None) -> dict[str, Any]:
    return control_page_ui_command_store.enqueue(action, panel_id=panel_id)


def build_control_page_panel_state() -> dict[str, Any]:
    return control_page_ui_command_store.panel_state()


def control_page_local_url() -> str:
    return f"http://{CONTROL_PAGE_HOST}:{CONTROL_PAGE_PORT}/"


def control_page_session_key(guild_id: int | None) -> str:
    if guild_id is None or int(guild_id) == LOCAL_CONTROL_GUILD_ID:
        return "control-page:local"
    return f"control-page:{guild_id}"


def control_page_effective_guild_id(guild: discord.Guild | None) -> int:
    return int(getattr(guild, "id", LOCAL_CONTROL_GUILD_ID) or LOCAL_CONTROL_GUILD_ID)


def control_page_effective_guild_name(guild: discord.Guild | None) -> str:
    if guild is None:
        return LOCAL_CONTROL_GUILD_NAME
    return clean_text(str(getattr(guild, "name", "") or "")) or LOCAL_CONTROL_GUILD_NAME


def append_control_page_chat_log(guild_id: int, role: str, author: str, text: str) -> None:
    control_page_chat_log_store.append(guild_id, role, author, text)


def get_control_page_chat_log(guild_id: int) -> list[dict[str, Any]]:
    return control_page_chat_log_store.get(guild_id)


def sanitize_control_page_welcome_text(text: str) -> str:
    return sanitize_control_page_welcome_text_payload(text, fallback=CONTROL_PAGE_WELCOME_FALLBACK)


async def generate_control_page_welcome_text(guild: discord.Guild | None) -> str:
    guild_name = control_page_effective_guild_name(guild)
    user_text = "컨트롤 페이지 첫 화면에 띄울 짧은 환영문구를 하나만 만들어줘."
    prompt = (
        "너는 이블린(E.V.E.L.Y.N)이다. 정훈이 컨트롤 페이지를 처음 열었을 때 보일 첫 말풍선을 만든다.\n"
        "조건:\n"
        "- 한국어 한 문장만 출력한다.\n"
        "- 18~55자 정도로 짧게 쓴다.\n"
        "- 살짝 재치있고 따뜻하지만 과장하지 않는다.\n"
        "- 명령어 설명, /memory 안내, 기능 소개, 마크다운, 따옴표, 이모지는 쓰지 않는다.\n"
        "- 현재 상태를 확인한 척하지 않는다.\n"
        f"- 현재 공간 이름: {guild_name}\n"
    )
    payload = build_main_llm_payload(
        model_name=MODEL_NAME,
        messages=[],
        final_user_text=prompt,
        source="control_page",
        stream=False,
        content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        temperature=0.65,
        max_tokens=72,
        stop_tokens=MAIN_LLM_STOP_TOKENS,
    )
    started_at = time.monotonic()
    try:
        session = await get_http_session()
        timeout = aiohttp.ClientTimeout(total=CONTROL_PAGE_WELCOME_LLM_TIMEOUT_SEC)
        async with session.post(LLM_SERVER_URL, json=payload, timeout=timeout) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(f"LLM 서버 오류: {resp.status} / {error_text[:300]}")
            data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("LLM returned empty choices")
        answer, _answer_source, _finish_reason = extract_main_llm_answer_from_choice(
            choices[0],
            user_text,
            sanitize_output=sanitize_model_output,
            parse_response_action_tag=parse_response_action_tag,
            extract_answer_from_reasoning=extract_answer_from_reasoning,
        )
        welcome = sanitize_control_page_welcome_text(answer)
        record_model_call_trace(
            model_role="main_llm",
            purpose="control_page_welcome",
            hot_path=False,
            started_at=started_at,
            success=True,
            model_name=MODEL_NAME,
            endpoint=LLM_SERVER_URL,
            source="control_page",
            guild_id=control_page_effective_guild_id(guild),
        )
        return welcome
    except Exception as exc:
        record_model_call_trace(
            model_role="main_llm",
            purpose="control_page_welcome",
            hot_path=False,
            started_at=started_at,
            success=False,
            error=exc,
            model_name=MODEL_NAME,
            endpoint=LLM_SERVER_URL,
            source="control_page",
            guild_id=control_page_effective_guild_id(guild),
        )
        print(f"[CONTROL PAGE] welcome_generation_failed err={exc!r}")
        return clean_text(CONTROL_PAGE_WELCOME_FALLBACK)


async def ensure_control_page_welcome_message(
    guild: discord.Guild | None,
    *,
    runtime_services: dict[str, Any] | None = None,
) -> None:
    services = runtime_services or {}
    if not bool(services.get("mainReady")):
        return
    guild_id = control_page_effective_guild_id(guild)
    if get_control_page_chat_log(guild_id):
        return
    lock = control_page_welcome_locks.setdefault(guild_id, asyncio.Lock())
    async with lock:
        if get_control_page_chat_log(guild_id):
            return
        welcome = await generate_control_page_welcome_text(guild)
        append_control_page_chat_log(guild_id, "assistant", "Evelyn", welcome)


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
    merged = merge_voyager_status_into_state(raw_status, observation) or {}
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
    merged["recent_activity"] = extract_minecraft_recent_activity_live(raw_status, base_limit=2)
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
    async def _voyager_alive() -> bool:
        return bool(await get_minecraft_client().is_service_alive(timeout_sec=0.45))

    return await probe_control_page_runtime_services(
        service_urls={
            "main": LLM_SERVER_URL,
            "router": ROUTER_LLM_URL,
            "sub": SUMMARY_LLM_URL,
            "tts": OMNIVOICE_SERVER_URL,
        },
        bot_api_host=CONTROL_PAGE_BOT_API_HOST,
        bot_api_port=CONTROL_PAGE_BOT_API_PORT,
        bot_api_state_path=CONTROL_PAGE_BOT_API_STATE_PATH,
        bot_api_probe_timeout_sec=CONTROL_PAGE_BOT_API_PROBE_TIMEOUT_SEC,
        action_backend=VOYAGER_ACTION_BACKEND,
        codex_gateway_port=VOYAGER_CODEX_GATEWAY_PORT,
        voyager_alive_probe=_voyager_alive,
    )


def _build_control_plane_runtime_services_snapshot(*, now: float | None = None) -> dict[str, Any]:
    return control_page_runtime_services_cache.snapshot_copy(
        refreshing=bool(
            control_page_runtime_services_refresh_task is not None
            and not control_page_runtime_services_refresh_task.done()
        ),
        now=now,
    )


def _can_schedule_control_page_runtime_services_refresh(*, now: float | None = None) -> bool:
    return control_page_runtime_services_cache.can_schedule_refresh(
        refreshing=bool(
            control_page_runtime_services_refresh_task is not None
            and not control_page_runtime_services_refresh_task.done()
        ),
        now=now,
    )


def _mark_control_page_runtime_services_refresh_request(*, now: float | None = None) -> None:
    control_page_runtime_services_cache.mark_refresh_request(now=now)


async def _refresh_control_page_runtime_services_cache_once() -> None:
    try:
        services = await _probe_control_page_runtime_services_once()
    except Exception as exc:
        error_text = clean_text(str(exc)) or type(exc).__name__
        services = build_control_page_runtime_services_error_payload(
            error_text,
            action_backend=VOYAGER_ACTION_BACKEND,
        )
    control_page_runtime_services_cache.store_success(services)


def _start_control_page_runtime_services_background_refresh(*, now: float | None = None) -> None:
    now_ts = time.time() if now is None else float(now)
    global control_page_runtime_services_refresh_task

    if not _can_schedule_control_page_runtime_services_refresh(now=now_ts):
        return
    _mark_control_page_runtime_services_refresh_request(now=now_ts)
    control_page_runtime_services_refresh_task = asyncio.create_task(
        _refresh_control_page_runtime_services_cache_once()
    )


async def get_control_page_runtime_services(*, force: bool = False) -> dict[str, Any]:
    global control_page_runtime_services_lock
    global control_page_runtime_services_refresh_task

    if control_page_runtime_services_lock is None:
        control_page_runtime_services_lock = asyncio.Lock()
    async with control_page_runtime_services_lock:
        now_ts = time.time()
        if control_page_runtime_services_cache.is_fresh(now=now_ts) and not force:
            return _build_control_plane_runtime_services_snapshot()
        if (not force) and control_page_runtime_services_cache.is_stale_not_expired(now=now_ts):
            _start_control_page_runtime_services_background_refresh(now=now_ts)
            return _build_control_plane_runtime_services_snapshot()
        await _refresh_control_page_runtime_services_cache_once()
        return _build_control_plane_runtime_services_snapshot()


def get_control_page_minecraft_snapshot_cache_copy() -> dict[str, Any]:
    return control_page_minecraft_snapshot_cache.snapshot_copy()


async def _refresh_control_page_minecraft_snapshot_once(guild_id: int | None) -> dict[str, Any]:
    try:
        snapshot = await asyncio.wait_for(
            get_control_page_minecraft_snapshot(guild_id),
            timeout=max(0.5, CONTROL_PAGE_MINECRAFT_SNAPSHOT_TIMEOUT_SEC),
        )
    except Exception as exc:
        error_text = clean_text(str(exc)) or repr(exc)
        return control_page_minecraft_snapshot_cache.store_error(error_text)

    return control_page_minecraft_snapshot_cache.store_success(snapshot)


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
        if not force and control_page_minecraft_snapshot_cache.is_fresh():
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
    global control_page_runtime_services_refresh_task
    for task in (
        control_page_minecraft_snapshot_poll_task,
        control_page_minecraft_snapshot_refresh_task,
        control_page_runtime_services_refresh_task,
    ):
        if task is not None and not task.done():
            task.cancel()
    control_page_minecraft_snapshot_poll_task = None
    control_page_minecraft_snapshot_refresh_task = None
    control_page_runtime_services_refresh_task = None


def build_control_page_status_text(guild: discord.Guild, minecraft: dict[str, Any]) -> str:
    vc = guild.voice_client
    voice_channel_name = getattr(getattr(vc, "channel", None), "name", None) or "없음"
    listening = bool(vc and hasattr(vc, "is_listening") and vc.is_listening())
    speaking = is_tracked_tts_playback_active(tts_playback_tracker, guild.id)
    tts_target = current_tts_target_name(guild) if speaking else "없음"
    return build_control_page_status_text_payload(
        guild_name=guild.name,
        voice_channel_name=voice_channel_name,
        listening=listening,
        speaking=speaking,
        tts_target=tts_target,
        voice_input_mode=voice_input_mode_status_line(),
        local_mic_status=local_mic_status_line(),
        main_model=MODEL_NAME,
        router_model=ROUTER_MODEL_NAME,
        summary_model=SUMMARY_MODEL_NAME,
        stt_model=STT_MODEL_NAME,
        minecraft=minecraft,
    )


def build_control_page_local_status_text(runtime_services: dict[str, Any] | None = None) -> str:
    local_tts = local_tts_playback_manager.snapshot()
    local_mic = serialize_local_mic_runtime_state()
    local_speaking = bool(local_tts.get("active"))
    local_listening = bool(local_mic.get("enabled") and local_mic.get("captureReady"))
    return build_control_page_local_status_text_payload(
        runtime_services,
        discord_enabled=DISCORD_ENABLED,
        local_url=control_page_local_url(),
        bot_api_host=CONTROL_PAGE_BOT_API_HOST,
        bot_api_port=CONTROL_PAGE_BOT_API_PORT,
        main_model=MODEL_NAME,
        router_model=ROUTER_MODEL_NAME,
        summary_model=SUMMARY_MODEL_NAME,
        stt_model=STT_MODEL_NAME,
        local_speaking=local_speaking,
        local_listening=local_listening,
        local_mic_status=local_mic_status_line(),
    )


async def build_control_page_status_reply(guild: discord.Guild) -> str:
    minecraft = await safe_get_control_page_minecraft_snapshot(guild.id)
    return build_control_page_status_text(guild, minecraft)


def build_control_page_voice_status_reply(guild: discord.Guild | None) -> str:
    vc = guild.voice_client if guild is not None else None
    voice = build_voice_pipeline_snapshot(guild)
    channel_name = getattr(getattr(vc, "channel", None), "name", None) or "none"
    continuity = voice.get("bargeInContinuity") if isinstance(voice.get("bargeInContinuity"), dict) else {}
    return build_control_page_voice_status_reply_payload(
        voice,
        channel_name=channel_name,
        voice_input_mode=voice_input_mode_status_line(),
        local_mic_status=local_mic_status_line(),
        continuity_detail_lines=_format_voice_barge_in_continuity_detail_lines(continuity),
    )


def build_control_page_voice_continuity_reply(guild: discord.Guild | None) -> str:
    _ = guild
    continuity = _build_voice_barge_in_continuity_snapshot()
    return build_control_page_voice_continuity_reply_payload(
        _format_voice_barge_in_continuity_detail_lines(continuity)
    )


async def build_control_page_inventory_reply(guild: discord.Guild) -> str:
    minecraft = await safe_get_control_page_minecraft_snapshot(guild.id)
    return build_control_page_inventory_reply_payload(minecraft)


async def build_control_page_minecraft_reply(guild: discord.Guild) -> str:
    minecraft = await safe_get_control_page_minecraft_snapshot(guild.id)
    return build_control_page_minecraft_reply_payload(minecraft)


def build_control_page_autonomy_reply(guild: discord.Guild) -> str:
    engine = autonomy_engines.get(guild.id)
    if engine is None:
        return "자율 행동 엔진이 아직 만들어지지 않았어."
    state = engine.state
    router = get_routed_autonomy_executor(guild.id)
    return build_control_page_autonomy_reply_payload(
        status=state.status,
        safety_mode=state.safety_mode,
        goal=state.current_goal.summary if state.current_goal else "없음",
        plan=state.current_plan.summary if state.current_plan else "없음",
        drive=state.drive_state if isinstance(state.drive_state, dict) else {},
        failure_count=state.failure_count,
        last_error=state.last_error,
        minecraft_enabled=bool(router and router.is_domain_enabled("minecraft")),
        allowed_actions=list(state.allowed_actions or []),
    )


def execute_control_page_memory_panel_action(action: str) -> str:
    cleaned_action = clean_text(action).lower()
    if cleaned_action not in {"open", "close", "toggle"}:
        cleaned_action = "toggle"
    enqueue_control_page_ui_command(cleaned_action, panel_id="memory")
    return memory_panel_reply(cleaned_action)


def execute_control_page_restart_command() -> str:
    asyncio.create_task(restart_bot_process())
    return "응, 이블린 다시 시작할게. 잠깐만 기다려줘."


def recent_control_page_history_for_router(*, session_key: str, guild_id: int | None, limit: int = 6) -> str:
    return session_state_store.recent_history_for_router(
        system_prompt=SYSTEM_PROMPT,
        session_key=session_key,
        guild_id=guild_id,
        limit=limit,
    )


def remember_control_page_tool_turn(
    guild: discord.Guild | None,
    user_text: str,
    reply_text: str,
    decision: dict[str, Any],
) -> None:
    guild_id = control_page_effective_guild_id(guild)
    session_key = control_page_session_key(guild_id)
    session_state_store.record_tool_assistant_turn(
        session_key,
        user_text,
        reply_text,
        tool_name=clean_text(str(decision.get("tool") or "")),
        system_prompt=SYSTEM_PROMPT,
        max_history_items=MAX_HISTORY,
        guild_id=guild_id,
        ttl_sec=ACTIVE_CONVERSATION_TEXT_SEC,
    )


async def decide_control_page_tool_call(text: str, *, guild_id: int | None, session_key: str) -> dict[str, Any] | None:
    if not ROUTER_LLM_ENABLED:
        return None
    user_text = clean_text(text)
    if not user_text:
        return None
    messages = [
        {
            "role": "system",
            "content": (
                "You are Evelyn's control-page tool router. "
                "Only classify ambiguous short control-page commands. "
                "Return exactly one JSON object and no other text. "
                "Available allowlisted tools: "
                f"{control_page_tool_registry_prompt()}. "
                "The router may choose only these tools; never invent tools, shell commands, paths, or code. "
                "For control_page.memory_panel, arguments must be {\"action\":\"open|close|toggle\"}. "
                "If the user is clearly asking for a tool, return "
                '{"tool_call":{"name":"control_page.memory_panel","arguments":{"action":"open"}},"confidence":0.92,"reply":"응, 메모리 패널 열어둘게."}. '
                "If no UI tool should be called, return "
                '{"tool_call":null,"confidence":0.0,"reply":""}. '
                "Do not call a tool for ordinary questions, explanations, styling requests, implementation requests, or discussion. "
                "Never call high-risk tools; ask for explicit slash commands instead. "
                "When you do call a UI tool, write reply in Evelyn's style: Korean, warm and sharp, casual 반말, "
                "one short sentence, no stiff '~습니다' or '~입니다' endings, no extra explanation."
            ),
        },
        {
            "role": "system",
            "content": "Recent conversation:\n" + (recent_control_page_history_for_router(session_key=session_key, guild_id=guild_id) or "(none)"),
        },
        {
            "role": "user",
            "content": user_text,
        },
    ]
    try:
        return await ask_router_llm(
            messages,
            max_tokens=180,
            timeout_seconds=min(ROUTER_ROUTE_TIMEOUT_SEC, 2.0),
            purpose="control_page_ui_tool",
            hot_path=True,
            turn_id=current_turn_id(session_key),
            session_key=session_key,
            source="control_page",
            guild_id=guild_id,
        )
    except Exception as exc:
        print(f"[CONTROL PAGE TOOL ROUTER] failed: {exc!r}")
        return None


async def decide_control_page_ui_tool_call(text: str, *, guild_id: int | None, session_key: str) -> dict[str, Any] | None:
    return await decide_control_page_tool_call(text, guild_id=guild_id, session_key=session_key)


async def execute_control_page_tool(guild: discord.Guild | None, decision: dict[str, Any]) -> str:
    policy_error = control_page_tool_policy_error(decision, guild_available=guild is not None)
    if policy_error:
        return policy_error
    tool_name = clean_text(str(decision.get("tool") or ""))
    arguments = decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}
    if tool_name == "control_page.help":
        return build_control_page_help_reply()
    memory_reply = await execute_control_page_memory_tool(
        tool_name,
        arguments,
        execute_memory_panel_action=execute_control_page_memory_panel_action,
        enqueue_ui_command=enqueue_control_page_ui_command,
        ensure_vault_layout=ensure_memory_vault_layout,
        open_vault_tool_reply=control_page_open_memory_vault_tool_reply,
        vault_obsidian_url=memory_vault_obsidian_url,
        open_url=open_control_page_url_with_system,
        open_path=open_control_page_path_with_system,
    )
    if memory_reply is not None:
        return memory_reply
    runtime_reply = await execute_control_page_runtime_tool(
        tool_name,
        guild=guild,
        get_runtime_services=get_control_page_runtime_services,
        build_local_status_text=build_control_page_local_status_text,
        build_status_reply=build_control_page_status_reply,
        execute_restart_command=execute_control_page_restart_command,
        schedule_local_shutdown=schedule_evelyn_local_shutdown,
        schedule_stack_shutdown=schedule_evelyn_stack_shutdown,
        schedule_bot_shutdown=lambda: asyncio.create_task(shutdown_bot_process()),
        build_autonomy_reply=build_control_page_autonomy_reply,
    )
    if runtime_reply is not None:
        return runtime_reply
    voice_reply = await execute_control_page_voice_tool(
        tool_name,
        arguments,
        guild=guild,
        build_voice_status_reply=build_control_page_voice_status_reply,
        set_input_mode=set_voice_input_mode,
        input_mode_status_line=voice_input_mode_status_line,
        restore_voice_channel=restore_last_voice_channel,
        build_voice_continuity_reply=build_control_page_voice_continuity_reply,
        reset_continuity_probe=reset_voice_barge_in_continuity_probe,
    )
    if voice_reply is not None:
        return voice_reply
    minecraft_reply = await execute_control_page_minecraft_tool(
        tool_name,
        arguments,
        guild=guild,
        build_inventory_reply=build_control_page_inventory_reply,
        build_minecraft_reply=build_control_page_minecraft_reply,
        enable_mode=enable_minecraft_mode,
        disable_mode=disable_minecraft_mode,
        get_client=get_minecraft_client,
        format_position=format_position_short,
    )
    if minecraft_reply is not None:
        return minecraft_reply
    return "그 명령은 등록만 되어 있고 실행기가 아직 없어."


async def execute_control_page_command(guild: discord.Guild | None, text: str) -> str:
    decision = cheap_control_page_tool_decision(text)
    if decision is not None:
        return await execute_control_page_tool(guild, decision)
    return "지원하지 않는 명령어야. /help 로 현재 페이지 명령어를 확인해줘."


async def answer_control_page_search_text(guild: discord.Guild | None, user_text: str) -> str:
    guild_id = control_page_effective_guild_id(guild)
    session_key = control_page_session_key(guild_id)
    messages = list(get_conversation_history(session_key=session_key, guild_id=guild_id))
    route_decision = build_route_decision(
        action="search_then_answer",
        route="search_executor",
        source="control_page",
        prompt_text=user_text,
        needs_main_llm=False,
        needs_search=True,
        needs_tts=False,
        priority="accuracy",
    )
    metrics: dict[str, Any] = {
        "started_at": time.monotonic(),
        "meta": {
            "source": "control_page",
            "session_key": session_key,
            "guild_id": guild_id,
            "selected_path": "control_page_search_direct",
        },
        "marks": {},
    }
    action_result = await execute_search_then_answer_action(
        guild_id=guild_id,
        user_text=user_text,
        session_key=session_key,
        messages=messages,
    )
    final_answer = await synthesize_tool_result_with_main_llm(
        user_text=user_text,
        tool_name="search",
        tool_result_text=action_result.answer_text,
        guild_id=guild_id,
        session_key=session_key,
        source="control_page",
        messages=messages,
        cognitive_state={"action": "search_then_answer", "user_intent": user_text},
        route_decision=route_decision,
        metrics=metrics,
    )
    reply = clean_text(final_answer) or clean_text(action_result.answer_text) or "지금 검색 결과를 정리하지 못했어. 잠깐 뒤에 다시 시도해줘."
    async with session_locks.setdefault(session_key, asyncio.Lock()):
        append_history(session_key, user_text, reply, guild_id=guild_id)
        mark_session_active(
            session_key,
            ttl_sec=ACTIVE_CONVERSATION_TEXT_SEC,
            speaker="assistant",
            awaiting_user_reply=False,
            topic_id=build_topic_id(user_text, "search_executor", reply),
            answer_text=reply,
            user_text=user_text,
        )
    schedule_local_control_tts(
        reply,
        turn_id=current_turn_id(session_key),
        session_key=session_key,
    )
    return format_display_text(reply, session_key=session_key).strip() or fallback_answer_for(user_text)


async def answer_control_page_text(guild: discord.Guild | None, user_text: str) -> str:
    guild_id = control_page_effective_guild_id(guild)
    session_key = control_page_session_key(guild_id)
    state_lock = session_locks.setdefault(session_key, asyncio.Lock())
    turn_id = ""
    topic_id = ""
    async with state_lock:
        started_turn = begin_user_text_turn(session_key, user_text, guild_id=guild_id)
        turn_id = started_turn.turn_id
        topic_id = started_turn.topic_id
    turn_scope = TurnScope(turn_id)
    replace_room_turn_scope(session_key, turn_scope)
    turn_task = _attach_current_task(turn_scope)
    text_metrics: dict[str, Any] = {
        "started_at": time.monotonic(),
        "meta": {
            "turn_id": turn_id,
            "source": "control_page",
            "session_key": session_key,
            "guild_id": guild_id,
            "topic_id": topic_id,
            "turn_type": "control_page_text",
            "selected_path": "control_page_local",
            "needs_tts": False,
        },
        "marks": {},
    }
    proactive_resolution = resolve_pending_proactive_question_for_turn(
        guild_id,
        user_text,
        session_key=session_key,
        session_memory_key=session_key,
        metrics=text_metrics,
    )
    answer = ""
    text_turn_summary_logged = False
    try:
        answer = await ask_llm_streaming(
            user_text,
            guild_id=guild_id,
            session_key=session_key,
            source="control_page",
            debug_text=user_text,
            metrics=text_metrics,
            turn_scope=turn_scope,
        )
        vision_capture_error = clean_text(str(text_metrics.get("meta", {}).get("vision_capture_error") or ""))
        if "black frame" in vision_capture_error.lower():
            answer = (
                "지금 화면 캡처가 검은 프레임으로 들어와서 실제 화면 분석은 못 했어. "
                "비전 모델 문제가 아니라 Windows 캡처 세션이 검은 이미지를 주는 상태야."
        )
        plain_answer = strip_omnivoice_tags(answer) or answer
        awaiting_reply = bool(session_state_snapshot(session_key).get("awaiting_user_reply"))
        proactive_asked = False
        if not proactive_resolution.get("resolved"):
            plain_answer, proactive_asked = maybe_append_proactive_question(
                plain_answer,
                guild_id=guild_id,
                source="control_page",
                user_text=user_text,
                awaiting_user_reply=awaiting_reply,
                session_key=session_key,
                session_memory_key=session_key,
                metrics=text_metrics,
            )
        if proactive_asked:
            answer = plain_answer
            awaiting_reply = True
        async with state_lock:
            finish_assistant_text_turn(
                session_key,
                user_text,
                plain_answer,
                guild_id=guild_id,
                awaiting_user_reply=awaiting_reply,
                topic_id=topic_id,
            )
        log_voice_bottleneck_summary(
            text_metrics,
            label="text_turn",
            extra=f"control_page=true chars={len(format_display_text(answer, session_key=session_key).strip())}",
            event_name="text_turn_summary",
        )
        text_turn_summary_logged = True
        schedule_local_control_tts(
            plain_answer,
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
        )
        return format_display_text(plain_answer, session_key=session_key).strip() or fallback_answer_for(user_text)
    finally:
        if text_metrics and not text_turn_summary_logged:
            text_metrics.setdefault("meta", {})["error_layer"] = "control_page_text"
            text_metrics.setdefault("meta", {}).setdefault("error", "control_page_text_aborted_before_summary")
            log_voice_bottleneck_summary(
                text_metrics,
                label="text_turn",
                extra="control_page=true error=true",
                event_name="text_turn_summary",
            )
        _detach_task(turn_scope, turn_task)
        clear_room_turn_scope(session_key, turn_scope)


async def handle_control_page_input(guild: discord.Guild | None, text: str) -> str:
    guild_id = control_page_effective_guild_id(guild)
    session_key = control_page_session_key(guild_id)
    cheap_decision = cheap_control_page_tool_decision(text)
    if cheap_decision is not None:
        reply = await execute_control_page_tool(guild, cheap_decision)
        remember_control_page_tool_turn(guild, text, reply, cheap_decision)
        return reply
    if clean_text(text).startswith("/"):
        return "지원하지 않는 명령어야. /help 로 현재 페이지 명령어를 확인해줘."
    if should_route_control_page_tool_candidate(text):
        tool_decision_raw = await decide_control_page_tool_call(text, guild_id=guild_id, session_key=session_key)
        tool_decision = control_page_tool_decision_from_llm(tool_decision_raw)
        if tool_decision:
            router_policy_error = control_page_tool_policy_error(tool_decision, guild_available=guild is not None)
            if router_policy_error:
                remember_control_page_tool_turn(guild, text, router_policy_error, tool_decision)
                return router_policy_error
            execute_reply = await execute_control_page_tool(guild, tool_decision)
            final_reply = control_page_tool_reply_from_execution(tool_decision, execute_reply)
            remember_control_page_tool_turn(guild, text, final_reply, tool_decision)
            return final_reply
        if isinstance(tool_decision_raw, dict):
            router_reply = clean_text(str(tool_decision_raw.get("reply") or ""))
            if router_reply:
                return router_reply
    if should_force_search_query(text):
        return await answer_control_page_search_text(guild, text)
    return await answer_control_page_text(guild, text)


STARTUP_BOOT_STEPS: tuple[tuple[str, str], ...] = (
    ("main_service", "Main LLM"),
    ("router_service", "Router LLM"),
    ("sub_service", "Sub LLM"),
    ("tts_service", "TTS service"),
    ("discord_gateway", "Discord gateway"),
    ("control_api", "Bot API"),
    ("opus", "Opus"),
    ("stt", "STT"),
    ("main_warmup", "Main LLM warmup"),
    ("tts_warmup", "TTS warmup"),
)


def mark_startup_component(key: str, status: str, detail: str = "") -> None:
    startup_component_state[key] = {
        "status": clean_text(status) or "pending",
        "detail": clean_text(detail),
        "updatedAt": time.time(),
    }


def startup_component_done(key: str) -> bool:
    return (startup_component_state.get(key) or {}).get("status") == "done"


def build_control_page_boot_progress(
    runtime_services: dict[str, Any] | None,
    *,
    guild_available: bool,
    listening: bool = False,
) -> dict[str, Any]:
    return build_control_page_boot_progress_payload(
        runtime_services,
        startup_steps=STARTUP_BOOT_STEPS,
        startup_component_state=startup_component_state,
        startup_components_ready=startup_components_ready,
        discord_enabled=DISCORD_ENABLED,
        discord_ready=bot.is_ready(),
        guild_available=guild_available,
        control_api_available=control_page_runner is not None,
        listening=listening,
    )


async def build_control_page_state(guild: discord.Guild | None) -> dict[str, Any]:
    return await build_control_page_state_from_runtime(
        guild,
        ControlPageStateDeps(
            get_runtime_services=get_control_page_runtime_services,
            is_control_api_ready=is_control_api_ready_from_runtime_services,
            build_runtime_health=build_control_page_runtime_health,
            discord_enabled=DISCORD_ENABLED,
            local_only_mode=LOCAL_ONLY_MODE,
            local_control_guild_id=LOCAL_CONTROL_GUILD_ID,
            local_control_guild_name=LOCAL_CONTROL_GUILD_NAME,
            ensure_welcome_message=ensure_control_page_welcome_message,
            build_commands=build_control_page_commands,
            build_all_commands=build_control_page_all_commands,
            build_boot_progress=build_control_page_boot_progress,
            local_tts_snapshot=local_tts_playback_manager.snapshot,
            serialize_local_mic_state=serialize_local_mic_runtime_state,
            read_vision_watch_state=read_vision_watch_state,
            build_panel_state=build_control_page_panel_state,
            local_url=control_page_local_url,
            get_chat_log=get_control_page_chat_log,
            build_voice_pipeline_snapshot=build_voice_pipeline_snapshot,
            main_model=MODEL_NAME,
            router_model=ROUTER_MODEL_NAME,
            summary_model=SUMMARY_MODEL_NAME,
            stt_model=STT_MODEL_NAME,
            inflight_llm_requests=inflight_llm_requests,
            tracked_tts_count=lambda: tracked_tts_playback_count(tts_playback_tracker),
            local_tts_enabled=lambda: local_tts_playback_manager.enabled,
            summarize_model_call_metrics=summarize_model_call_metrics,
            summarize_question_metrics=summarize_question_metrics,
            build_local_status_text=build_control_page_local_status_text,
            ensure_minecraft_snapshot=ensure_control_page_minecraft_snapshot,
            minecraft_snapshot_has_value=control_page_minecraft_snapshot_cache.has_snapshot,
            minecraft_snapshot_copy=get_control_page_minecraft_snapshot_cache_copy,
            is_tts_active=lambda guild_id: is_tracked_tts_playback_active(tts_playback_tracker, guild_id),
            current_tts_target_name=current_tts_target_name,
            serialize_local_mic_target=serialize_local_mic_target,
            resolve_local_mic_target=resolve_local_mic_target,
            guilds=bot.guilds,
            local_mic_discord_user_ids=LOCAL_MIC_DISCORD_USER_IDS,
            voice_debug_audio=VOICE_DEBUG_SAVE_AUDIO,
            build_status_text=build_control_page_status_text,
        ),
    )


async def control_page_index_handler(_: web.Request) -> web.StreamResponse:
    return control_page_file_response(
        CONTROL_PAGE_DOCS_DIR / "index.html",
        not_found_text="control page index not found",
    )


async def control_page_asset_handler(request: web.Request) -> web.StreamResponse:
    return control_page_file_response(
        resolve_control_page_asset_path(CONTROL_PAGE_ASSETS_DIR, request.match_info.get("asset_path", "")),
        not_found_text="asset not found",
    )


async def control_page_minecraft_item_icon_handler(request: web.Request) -> web.StreamResponse:
    item_name = normalize_minecraft_item_name(request.match_info.get("item_name", ""))
    if not item_name:
        raise web.HTTPNotFound(text="item icon not found")
    icon_bytes = control_page_minecraft_item_icon_loader.load_icon(item_name)
    if not icon_bytes:
        raise web.HTTPNotFound(text="item icon not found")
    response = web.Response(body=icon_bytes, content_type="image/png")
    return add_control_page_no_store_headers(response)


async def control_page_state_handler(request: web.Request) -> web.StreamResponse:
    guild = select_control_page_guild(parse_control_page_guild_id(request.query.get("guildId")))
    return control_page_json_response(await build_control_page_state(guild))


async def control_page_chat_handler(request: web.Request) -> web.StreamResponse:
    try:
        payload = await request.json()
    except Exception:
        return control_page_json_response({"ok": False, "error": "invalid_json"}, status=400)
    response_payload, status = await handle_control_page_chat_request(
        payload,
        discord_enabled=DISCORD_ENABLED,
        select_guild=select_control_page_guild,
        effective_guild_id=control_page_effective_guild_id,
        append_chat_log=append_control_page_chat_log,
        handle_input=handle_control_page_input,
        ensure_minecraft_snapshot=ensure_control_page_minecraft_snapshot,
        refresh_runtime_services=get_control_page_runtime_services,
        build_state=build_control_page_state,
    )
    return control_page_json_response(response_payload, status=status)


async def control_page_memory_graph_handler(request: web.Request) -> web.StreamResponse:
    params = parse_control_page_memory_graph_query(request.query)
    return control_page_json_response(export_memory_graph(**params))


async def control_page_memory_snapshot_handler(request: web.Request) -> web.StreamResponse:
    return control_page_json_response(memory_vault_user_snapshot(**parse_control_page_memory_snapshot_query(request.query)))


async def control_page_memory_note_handler(request: web.Request) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    result = memory_vault_user_note(note_id, **parse_control_page_memory_note_query(request.query))
    return control_page_json_response(result, status=control_page_result_status(result))


async def control_page_memory_note_action_handler(request: web.Request) -> web.StreamResponse:
    note_id = request.match_info.get("note_id", "")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    result, status = handle_control_page_memory_note_action_request(
        note_id,
        payload,
        update_note=update_memory_vault_user_note,
    )
    return control_page_json_response(result, status=status)


async def control_page_shutdown_handler(request: web.Request) -> web.StreamResponse:
    response_payload, status = await handle_control_page_shutdown_request(
        request.query.get("guildId"),
        select_guild=select_control_page_guild,
        handle_input=handle_control_page_input,
        build_state=build_control_page_state,
    )
    return control_page_json_response(response_payload, status=status)


async def control_page_health_handler(_: web.Request) -> web.StreamResponse:
    return control_page_json_response(
        build_control_page_health_payload(
            local_only_mode=LOCAL_ONLY_MODE,
            discord_enabled=DISCORD_ENABLED,
            port=CONTROL_PAGE_PORT,
        )
    )


def open_control_page_path_with_system(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_control_page_url_with_system(url: str) -> None:
    if os.name == "nt":
        os.startfile(url)  # type: ignore[attr-defined]
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def control_page_open_memory_vault_handler(request: web.Request) -> web.StreamResponse:
    _ = request
    vault = ensure_memory_vault_layout()
    obsidian_url = memory_vault_obsidian_url(vault)
    payload, status = control_page_open_memory_vault_result(
        vault_path=vault,
        obsidian_url=obsidian_url,
        open_url=open_control_page_url_with_system,
        open_path=open_control_page_path_with_system,
    )
    return control_page_json_response(payload, status=status)


async def control_page_open_memory_vault_options_handler(request: web.Request) -> web.StreamResponse:
    _ = request
    return control_page_json_response({"ok": True, "methods": ["POST", "OPTIONS"]})


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
        app.router.add_get("/health", control_page_health_handler)
        app.router.add_get("/", control_page_index_handler)
        app.router.add_get("/assets/{asset_path:.*}", control_page_asset_handler)
        app.router.add_get(CONTROL_PAGE_MINECRAFT_ICON_ROUTE + "/{item_name}", control_page_minecraft_item_icon_handler)
        app.router.add_get("/api/control-page/state", control_page_state_handler)
        app.router.add_get("/api/control-page/memory", control_page_memory_snapshot_handler)
        app.router.add_get("/api/control-page/memory-graph", control_page_memory_graph_handler)
        app.router.add_get("/api/control-page/memory/{note_id}", control_page_memory_note_handler)
        app.router.add_post("/api/control-page/open-memory-vault", control_page_open_memory_vault_handler)
        app.router.add_post("/api/control-page/chat", control_page_chat_handler)
        app.router.add_post("/api/control-page/memory/{note_id}", control_page_memory_note_action_handler)
        app.router.add_post("/api/control-page/shutdown", control_page_shutdown_handler)
        app.router.add_options("/api/control-page/state", control_page_state_handler)
        app.router.add_options("/api/control-page/memory", control_page_memory_snapshot_handler)
        app.router.add_options("/api/control-page/memory-graph", control_page_memory_graph_handler)
        app.router.add_options("/api/control-page/memory/{note_id}", control_page_memory_note_handler)
        app.router.add_options("/api/control-page/open-memory-vault", control_page_open_memory_vault_options_handler)
        app.router.add_options("/api/control-page/chat", control_page_chat_handler)
        app.router.add_options("/api/control-page/shutdown", control_page_shutdown_handler)
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
        mark_startup_component("control_api", "done", control_page_local_url())
        print(f"[CONTROL PAGE] live url={control_page_local_url()}")


def build_voice_route_execution_deps() -> VoiceRouteExecutionDeps:
    return VoiceRouteExecutionDeps(
        update_session_state=update_session_state,
        emit_delivery_plan_chunks=emit_delivery_plan_chunks,
        build_delivery_plan=build_delivery_plan,
        split_tts_sentences=split_tts_sentences,
        build_search_query=build_search_query,
        search_duckduckgo=search_duckduckgo,
        answer_from_search_results=answer_from_search_results,
        prepare_llm_messages=prepare_llm_messages,
        policy_response_for_state=policy_response_for_state,
        build_route_decision_from_state=build_route_decision_from_state,
        apply_ask_gating=apply_ask_gating,
        build_route_decision=build_route_decision,
        apply_fast_path_question_policy=apply_fast_path_question_policy,
        should_await_user_reply_for_route=should_await_user_reply_for_route,
        answer_simple_local_chat_query=answer_simple_local_chat_query,
        answer_current_datetime_query=answer_current_datetime_query,
        answer_gpu_runtime_status_query=answer_gpu_runtime_status_query,
        synthesize_tool_result_with_main_llm=synthesize_tool_result_with_main_llm,
        observe_live_minecraft_state=observe_live_minecraft_state,
        skill_registry=skill_registry,
        recent_skill_dispatches=recent_skill_dispatches,
        build_main_response_guidance=build_main_response_guidance,
        build_main_llm_payload=build_main_llm_payload,
        execute_main_llm_once=execute_main_llm_once,
        build_answer_payload_from_text=build_answer_payload_from_text,
        resolve_route_executor=resolve_route_executor,
        model_name=MODEL_NAME,
        main_llm_stop_tokens=tuple(MAIN_LLM_STOP_TOKENS),
        voice_llm_max_tokens=VOICE_LLM_MAX_TOKENS,
        default_internal_routes=DEFAULT_INTERNAL_ROUTES,
        disabled_main_app_skill_routes=DISABLED_MAIN_APP_SKILL_ROUTES,
        skill_dispatch_cache_ttl_sec=SKILL_DISPATCH_CACHE_TTL_SEC,
        skill_dispatch_repeat_window_sec=SKILL_DISPATCH_REPEAT_WINDOW_SEC,
        skill_dispatch_cache_max=SKILL_DISPATCH_CACHE_MAX,
        router_route_timeout_sec=ROUTER_ROUTE_TIMEOUT_SEC,
        cognitive_timeout_sec=COGNITIVE_TIMEOUT_SEC,
        router_llm_enabled=ROUTER_LLM_ENABLED,
        log=print,
    )


async def execute_search_then_answer_action(
    *,
    guild_id: int | None,
    user_text: str,
    session_key: str | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> ActionResult:
    return await execute_search_then_answer_action_with_deps(
        deps=build_voice_route_execution_deps(),
        guild_id=guild_id,
        user_text=user_text,
        session_key=session_key,
        messages=messages,
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
    return await prepare_route_context_with_deps(
        user_text,
        guild_id,
        deps=build_voice_route_execution_deps(),
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        debug_text=debug_text,
        metrics=metrics,
        turn_scope=turn_scope,
    )


async def maybe_handle_short_circuit_route(
    *,
    route_decision: RouteDecision,
    source: str,
    guild_id: int | None,
    user_text: str,
    session_key: str | None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    debug_text: str | None = None,
    on_sentence: Callable[[str], Awaitable[None]] | None = None,
    on_first_chunk: Callable[[], None] | None = None,
    awaiting_user_reply: bool = False,
    metrics: dict | None = None,
    messages: list[dict[str, Any]] | None = None,
    cognitive_state: dict | None = None,
) -> tuple[str | None, Callable[[], None] | None]:
    return await maybe_handle_short_circuit_route_with_deps(
        deps=build_voice_route_execution_deps(),
        route_decision=route_decision,
        source=source,
        guild_id=guild_id,
        user_text=user_text,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        debug_text=debug_text,
        on_sentence=on_sentence,
        on_first_chunk=on_first_chunk,
        awaiting_user_reply=awaiting_user_reply,
        metrics=metrics,
        messages=messages,
        cognitive_state=cognitive_state,
    )


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
    return await maybe_execute_registered_route_with_deps(
        deps=build_voice_route_execution_deps(),
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
        allow_internal_routes=allow_internal_routes,
    )

def increment_inflight_llm_requests() -> None:
    global inflight_llm_requests
    inflight_llm_requests += 1


def decrement_inflight_llm_requests() -> None:
    global inflight_llm_requests
    inflight_llm_requests = max(0, inflight_llm_requests - 1)


def build_voice_main_llm_streaming_deps() -> VoiceMainLlmStreamingDeps:
    return VoiceMainLlmStreamingDeps(
        model_name=MODEL_NAME,
        llm_server_url=LLM_SERVER_URL,
        main_llm_chat_content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        voice_llm_max_tokens=VOICE_LLM_MAX_TOKENS,
        main_llm_stop_tokens=tuple(MAIN_LLM_STOP_TOKENS),
        get_http_session=get_http_session,
        is_casual_call_or_status_question=is_casual_call_or_status_question,
        observe_live_minecraft_state=observe_live_minecraft_state,
        build_runtime_status_context=build_runtime_status_context,
        build_main_response_guidance=build_main_response_guidance,
        mark_turn_stage=mark_turn_stage,
        build_main_llm_payload=build_main_llm_payload,
        build_stream_speech_chunker=build_stream_speech_chunker,
        user_explicitly_mentions_minecraft=user_explicitly_mentions_minecraft,
        extract_main_llm_answer_from_choice=extract_main_llm_answer_from_choice,
        sanitize_model_output=sanitize_model_output,
        parse_response_action_tag=parse_response_action_tag,
        extract_answer_from_reasoning=extract_answer_from_reasoning,
        ask_llm_once=ask_llm_once,
        resolve_promised_search_final_answer=resolve_promised_search_final_answer,
        enforce_question_limits=enforce_question_limits,
        record_question_trace=record_question_trace,
        emit_delivery_plan_chunks=emit_delivery_plan_chunks,
        build_delivery_plan=build_delivery_plan,
        build_answer_payload_from_text=build_answer_payload_from_text,
        split_tts_sentences=split_tts_sentences,
        decode_sse_stream_line=decode_sse_stream_line,
        answer_contains_minecraft_leak=answer_contains_minecraft_leak,
        emit_stream_delta_chunks=emit_stream_delta_chunks,
        record_model_call_trace=record_model_call_trace,
        sanitize_unrequested_minecraft_leak=sanitize_unrequested_minecraft_leak,
        flush_streamed_answer_chunks=flush_streamed_answer_chunks,
        increment_inflight_llm_requests=increment_inflight_llm_requests,
        decrement_inflight_llm_requests=decrement_inflight_llm_requests,
        log=print,
    )


async def execute_main_llm_streaming_turn(
    *,
    request: VoiceTurnRequest,
    route_context: VoiceTurnRouteContext,
    on_first_chunk: Callable[[], None] | None,
) -> str:
    return await execute_main_llm_streaming_turn_with_deps(
        deps=build_voice_main_llm_streaming_deps(),
        request=request,
        route_context=route_context,
        on_first_chunk=on_first_chunk,
    )

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
    return build_streaming_voice_delivery(
        DiscordStreamingVoiceDeliveryRequest(
            voice_client=vc,
            metrics=metrics,
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
            stream_tts_sentences=stream_tts_sentences,
            create_playback_task=lambda coro, scope: create_turn_scoped_task(coro, turn_scope=scope),
            log_stage=log_voice_stage,
            prefetch_chunks=TTS_PREFETCH_CHUNKS,
            log=print,
        )
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
    return await execute_streaming_voice_delivery_plan(
        delivery_plan,
        start_delivery=lambda: start_streaming_voice_delivery(
            vc,
            metrics=metrics,
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
        ),
    )


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
    try:
        await delivery.close(cleaned_answer)
        queued_sentence_count = await delivery.finalize()
        if cleaned_answer:
            _mark_voice_barge_in_continuity_probe(
                metrics,
                success=True,
                reason="finalize_complete",
                queued_sentence_count=queued_sentence_count,
            )
        else:
            _mark_voice_barge_in_continuity_probe(
                metrics,
                success=False,
                reason="finalize_empty_answer",
                queued_sentence_count=queued_sentence_count,
                reason_code=VOICE_BARGE_IN_REASON_CODE["FALSE_TRIGGER"],
                reason_label=VOICE_BARGE_IN_REASON_LABEL[VOICE_BARGE_IN_REASON_CODE["FALSE_TRIGGER"]],
            )
    except Exception as exc:
        error_text = f"{type(exc).__name__}:{clean_text(str(exc))}"
        _mark_voice_barge_in_continuity_probe(
            metrics,
            success=False,
            reason=f"finalize_exception:{error_text}",
            queued_sentence_count=0,
        )
        raise
    return cleaned_answer, queued_sentence_count


async def ask_llm_and_speak_local(
    _vc: Any,
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
        metrics.setdefault("meta", {})["output_mode"] = "local_speaker"
        metrics.setdefault("meta", {})["delivery_mode"] = "llm_sentence_stream"
        metrics.setdefault("tts_request_logged", False)
        metrics.setdefault("tts_response_headers_logged", False)
        metrics.setdefault("tts_first_byte_logged", False)
        metrics.setdefault("tts_first_frame_logged", False)
        metrics.setdefault("first_packet_sent_logged", False)
        metrics.setdefault("local_first_playback_logged", False)
        turn_id = metrics.get("meta", {}).get("turn_id") or current_turn_id(session_key)
        log_voice_stage(metrics, "LLM/local streaming TTS pipeline start", extra=f"source={source} mode=local_speaker_stream")

        delivery = start_streaming_local_voice_delivery(
            metrics=metrics,
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
        )
        fanout = ReplyStreamFanout([delivery])
        answer = ""
        cleaned_answer = ""
        queued_sentence_count = 0
        fallback_needed = False
        playback_count_before = int(local_tts_playback_manager.snapshot().get("playCount") or 0)
        try:
            answer = await ask_llm_streaming(
                user_text,
                guild_id=guild_id,
                session_key=session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                on_sentence=fanout.on_chunk,
                on_first_chunk=lambda: log_voice_latency(metrics, "llm_first_chunk_logged", "LLM first chunk"),
                source=source,
                debug_text=debug_text,
                metrics=metrics,
                turn_scope=turn_scope,
            )
            try:
                cleaned_answer, queued_sentence_count = await finalize_voice_answer(
                    answer,
                    on_final_answer=on_final_answer,
                    delivery=delivery,
                    metrics=metrics,
                )
            except Exception as exc:
                cleaned_answer = clean_text(answer)
                metrics.setdefault("meta", {})["local_streaming_tts_error"] = repr(exc)
                record_voice_pipeline_failure(
                    "tts_playback_failed",
                    exc,
                    metrics,
                    turn_id=turn_id,
                    session_key=session_key,
                    stage="local_speaker_stream_finalize",
                )
                fallback_needed = True
            playback_count_after = int(local_tts_playback_manager.snapshot().get("playCount") or 0)
            if queued_sentence_count <= 0 or playback_count_after <= playback_count_before:
                fallback_needed = True
                metrics.setdefault("meta", {})["local_streaming_tts_fallback_reason"] = (
                    "no_sentence_queued" if queued_sentence_count <= 0 else "no_local_playback"
                )
        finally:
            await delivery.abort()

        if fallback_needed and cleaned_answer:
            metrics.setdefault("meta", {})["local_streaming_tts_fallback_used"] = True
            await speak_answer_local(
                cleaned_answer,
                turn_id=turn_id,
                session_key=session_key,
                turn_scope=turn_scope,
                metrics=metrics,
            )

        log_voice_bottleneck_summary(
            metrics,
            label="voice_turn",
            extra=f"source={source} chars={len(cleaned_answer)} mode=local_speaker_stream sentences={queued_sentence_count} fallback={fallback_needed}",
            event_name="voice_turn_summary",
        )
        return cleaned_answer
    except asyncio.CancelledError:
        metrics.setdefault("meta", {})["playback_cancelled"] = True
        metrics.setdefault("meta", {})["error_layer"] = "voice_turn"
        metrics.setdefault("meta", {})["error"] = "cancelled"
        log_voice_bottleneck_summary(
            metrics,
            label="voice_turn",
            extra="cancelled=true mode=local_speaker",
            event_name="voice_turn_summary",
        )
        raise
    except Exception as exc:
        metrics.setdefault("meta", {})["error_layer"] = "voice_turn"
        metrics.setdefault("meta", {})["error"] = repr(exc)
        log_voice_bottleneck_summary(
            metrics,
            label="voice_turn",
            extra="error=true mode=local_speaker",
            event_name="voice_turn_summary",
        )
        raise
    finally:
        _detach_task(turn_scope, task)


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
    if is_local_speaker_voice_client(vc):
        return await ask_llm_and_speak_local(
            vc,
            user_text,
            guild_id=guild_id,
            on_final_answer=on_final_answer,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
            turn_scope=turn_scope,
        )

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
        metrics.setdefault("local_first_playback_logged", False)
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
    proactive_resolution: dict | None = None,
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
        awaiting_reply = bool(session_state_snapshot(session_key).get("awaiting_user_reply"))
        if proactive_resolution is not None:
            metrics.setdefault("meta", {})["proactive_question_resolution"] = proactive_resolution
        proactive_asked = False
        if not (proactive_resolution or {}).get("resolved"):
            answer, proactive_asked = maybe_append_proactive_question(
                answer,
                guild_id=guild_id,
                source=source,
                user_text=user_text,
                awaiting_user_reply=awaiting_reply,
                room_key=room_key,
                person_key=person_key,
                session_key=session_key,
                session_memory_key=session_memory_key,
                metrics=metrics,
            )
        if proactive_asked:
            update_session_state(
                session_key,
                speaker="assistant",
                awaiting_user_reply=True,
                answer_text=answer,
                user_text=user_text,
            )
        answer_payload = build_answer_payload_from_text(answer)
        final_text = format_display_text(answer_payload.display_text, session_key=session_key).strip() or fallback_answer_for(user_text)
        delivery_plan = build_delivery_plan(
            answer_payload,
            include_voice=include_voice,
            text_message=final_text,
            split_chunks=split_tts_sentences,
        )
        sent_message = (await send_discord_text(channel, final_text)).message
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
    debug_meta_input = normalize_voice_debug_meta(debug_meta)
    source = voice_ingress_source(debug_meta_input)
    if should_drop_discord_audio_for_local_mic(getattr(member, "id", None), source=source):
        return

    guild = getattr(member, "guild", None)
    if guild is None:
        return

    ensure_voice_worker_started()

    guild_id = guild.id
    voice_channel_id = getattr(getattr(guild.voice_client, "channel", None), "id", None)
    ingress = build_voice_ingress_context(
        guild_id=guild_id,
        voice_channel_id=voice_channel_id,
        user_id=member.id,
    )
    room_session_key = ingress.room_session_key
    session_key = ingress.session_key
    room_key = ingress.room_key
    person_key = ingress.person_key
    session_memory_key = ingress.session_memory_key
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
    await _schedule_voice_utterance_item(item)


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

        wake_interpretation = interpret_wake_probe_result(
            wake_result,
            clean_text=clean_text,
            apply_post_corrections=apply_stt_post_corrections,
        )
        wake_probe = wake_interpretation.probe_text
        wake_confirm = wake_interpretation.confirm_text
        wake_detected = wake_interpretation.wake_detected
        wake_match_mode = wake_interpretation.wake_match_mode
        wake_alias = wake_interpretation.wake_alias
        wake_reject_reason = wake_interpretation.wake_reject_reason
        print(
            f"[STT RESULT][wake] probe={wake_probe!r} confirm={wake_confirm!r} detected={wake_detected} mode={wake_match_mode} alias={wake_alias!r} reject={wake_reject_reason!r}"
        )

        strict_confirm_required = should_require_confirm_exact_for_wake(debug_meta)
        wake_interpretation = apply_strict_wake_confirm_policy(
            wake_interpretation,
            strict_confirm_required=strict_confirm_required,
        )
        wake_detected = wake_interpretation.wake_detected
        wake_match_mode = wake_interpretation.wake_match_mode
        wake_alias = wake_interpretation.wake_alias
        wake_reject_reason = wake_interpretation.wake_reject_reason

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
        wake_interpretation = apply_fuzzy_wake_near_miss(
            wake_interpretation,
            fuzzy_leading_wake_alias=fuzzy_leading_wake_alias,
        )
        wake_detected = wake_interpretation.wake_detected
        wake_match_mode = wake_interpretation.wake_match_mode
        wake_alias = wake_interpretation.wake_alias
        wake_reject_reason = wake_interpretation.wake_reject_reason
        if wake_interpretation.near_miss:
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

    interrupt_meta = TtsInterruptMeta(
        active_speaker_match=active_speaker_user_id == member.id,
        wake_detected=wake_detected,
        vad_prob=voice_like_prob,
        audio_sec=duration_sec,
        rms_ok=body_rms >= VOICE_WAVEFORM_BODY_RMS_MIN,
        voice_like=voice_like_prob >= 0.45,
    )
    qualified_tts_interrupt = should_interrupt_tts(interrupt_meta)
    local_tts_active = bool(LOCAL_ONLY_MODE and local_tts_playback_manager.snapshot().get("active"))
    tts_suppression = tts_playback_manager.input_suppression_reason(
        guild_id=guild_id,
        post_tts_ignore_sec=POST_TTS_IGNORE_SEC,
    )
    if qualified_tts_interrupt and (local_tts_active or tts_suppression == "bot_is_speaking"):
        speaker_verification = await verify_speaker_for_tts_interrupt(
            audio16k,
            sampling_rate=stt_sampling_rate,
            source=str(metrics.setdefault("meta", {}).get("ingress_source") or "discord_voice"),
            metrics=metrics,
        )
        if not speaker_verification_allows_tts_interrupt(speaker_verification):
            qualified_tts_interrupt = False
            register_drop_reason(
                metrics,
                "speaker_verification_rejected",
                session_key=session_key,
                room_session_key=room_session_key,
                owner_user_id=owner_user_id,
                wake_probe_text=wake_probe,
                wake_detected=wake_detected,
                speaker_verification=speaker_verification.to_dict(),
            )
            log_voice_stage(
                metrics,
                "speaker verification rejected TTS interrupt",
                extra=f"speaker={member.display_name} score={speaker_verification.score}",
            )
            log_voice_bottleneck_summary(
                metrics,
                label="voice_drop",
                extra="drop=speaker_verification_rejected",
                event_name="voice_drop_summary",
            )
            return
    if local_tts_active:
        if qualified_tts_interrupt:
            stopped = local_tts_playback_manager.request_stop(reason="qualified_user_audio")
            metrics.setdefault("meta", {})["local_tts_interrupted_by_user_audio"] = bool(stopped)
            if stopped:
                start_voice_barge_in_continuity_probe(metrics, source="local_tts")
                metrics.setdefault("meta", {})["tts_interrupted_at"] = time.monotonic()
                log_turn_event("tts_interrupt", guild_id=guild_id, reason="qualified_user_audio", output_mode="local_speaker")
                log_voice_stage(metrics, "로컬 TTS 사용자 발화로 중단", extra=f"speaker={member.display_name} wake_detected={wake_detected}")
        else:
            register_drop_reason(metrics, "local_tts_active_input_suppressed", session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, wake_probe_text=wake_probe, wake_detected=wake_detected)
            log_voice_stage(metrics, "로컬 TTS 재생 중 약한 입력 무시", extra=f"speaker={member.display_name} wake_detected={wake_detected}")
            log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=local_tts_active_input_suppressed", event_name="voice_drop_summary")
            return

    if tts_suppression == "bot_is_speaking" and qualified_tts_interrupt:
        await asyncio.sleep(TTS_INTERRUPT_DEBOUNCE_SEC)
        tts_suppression = tts_playback_manager.input_suppression_reason(
            guild_id=guild_id,
            post_tts_ignore_sec=POST_TTS_IGNORE_SEC,
        )
        if tts_suppression == "bot_is_speaking":
            stopped = await stop_active_tts_playback(guild_id, reason="qualified_user_audio")
            if stopped:
                start_voice_barge_in_continuity_probe(metrics, source="discord_voice")
                metrics.setdefault("meta", {}).update({
                    "tts_interrupted_by_user_audio": True,
                    "tts_interrupted_at": time.monotonic(),
                })
            tts_suppression = None
        elif tts_suppression is not None:
            register_drop_reason(metrics, tts_suppression, session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, wake_probe_text=wake_probe, wake_detected=wake_detected)
            stage_label = "디바운스 후 TTS 직후 입력 무시"
            log_voice_stage(metrics, stage_label, extra=f"speaker={member.display_name} wake_detected={wake_detected}")
            log_voice_bottleneck_summary(metrics, label="voice_drop", extra=f"drop={tts_suppression}", event_name="voice_drop_summary")
            return
    elif tts_suppression is not None:
        register_drop_reason(metrics, tts_suppression, session_key=session_key, room_session_key=room_session_key, owner_user_id=owner_user_id, wake_probe_text=wake_probe, wake_detected=wake_detected)
        stage_label = "봇 재생 중 약한 입력 무시" if tts_suppression == "bot_is_speaking" else "TTS 직후 입력 무시"
        log_voice_stage(metrics, stage_label, extra=f"speaker={member.display_name} wake_detected={wake_detected}")
        log_voice_bottleneck_summary(metrics, label="voice_drop", extra=f"drop={tts_suppression}", event_name="voice_drop_summary")
        return

    print(f"[FULL STT ENTER] speaker={member.display_name} sampling_rate={stt_sampling_rate} samples={audio16k.size} wake_detected={wake_detected}")
    log_voice_stage(metrics, "본문 STT 시작", extra=f"samples={audio16k.size}")
    stt_meta: dict | None = None
    partial_text = ""
    committed_partial_text = ""
    try:
        partial_result = await run_partial_stt_flow(
            audio16k,
            sampling_rate=stt_sampling_rate,
            session_key=session_key,
            timeout_sec=max(3.0, min(10.0, FULL_STT_TIMEOUT_SEC * 0.5)),
            build_partial_stt_window=build_partial_stt_window,
            get_partial_transcript=get_partial_transcript,
            read_committed_text=lambda key: session_committed_stt_text.get(key or "", ""),
            run_blocking_stt_task=run_blocking_stt_task,
            speculate_from_committed_stt=speculate_from_committed_stt,
            room_state=room_state_snapshot(room_session_key),
            clean_text=clean_text,
            metrics=metrics,
            print_fn=print,
        )
        partial_text = partial_result.partial_text
        committed_partial_text = partial_result.committed_text
        metrics.setdefault("meta", {}).update({
            "partial_stt_text": partial_text,
            "committed_stt_text": committed_partial_text,
        })
        speculative = partial_result.speculative_policy
        if speculative is not None:
            remember_speculative_policy(session_key, speculative)
            metrics.setdefault("meta", {})["speculative_policy"] = dict(speculative.get("policy") or {})
    except Exception as e:
        print(f"[STT PARTIAL] {e}")

    try:
        full_stt_result = await run_full_stt_with_optional_rescore(
            audio16k,
            sampling_rate=stt_sampling_rate,
            duration_sec=duration_sec,
            wake_probe=wake_probe,
            max_new_tokens=VOICE_STT_MAX_NEW_TOKENS,
            full_timeout_sec=FULL_STT_TIMEOUT_SEC,
            rescore_enabled=STT_FULL_RESCORING_ENABLED,
            rescore_extra_tokens=STT_FULL_RESCORE_EXTRA_TOKENS,
            rescore_min_audio_sec=STT_FULL_RESCORING_MIN_AUDIO_SEC,
            rescore_min_text_len=STT_FULL_RESCORING_MIN_TEXT_LEN,
            rescore_timeout_sec=STT_FULL_RESCORING_TIMEOUT_SEC,
            run_blocking_stt_task=run_blocking_stt_task,
            transcribe_audio=transcribe_audio16k_sync,
            choose_candidate=lambda primary, rescore: choose_full_stt_candidate(primary, rescore, wake_probe=wake_probe),
            clean_text=clean_text,
            log_stage=log_voice_stage,
            metrics=metrics,
            print_fn=print,
            speaker_name=member.display_name,
        )
    except Exception as e:
        print(f"[STT] {e}")
        log_voice_stage(metrics, "본문 STT 실패", extra=repr(e))
        return

    text = full_stt_result.text
    stt_meta = full_stt_result.stt_meta

    mark_turn_stage(metrics, "stt_full_done", event_name="stt_full_done", text_len=len(text))
    log_voice_stage(metrics, "본문 STT 완료", extra=f"text_len={len(text)}", key="stt_done")

    if not text:
        save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text="[EMPTY STT]", debug_meta=debug_meta, stt_meta=stt_meta, session_key=session_key, stage_label="drop")
        log_voice_stage(metrics, "본문 STT 빈 결과")
        return

    final_transcript = build_final_transcript_flow(
        text=text,
        partial_text=partial_text,
        session_key=session_key,
        wake_detected=wake_detected,
        wake_match_mode=wake_match_mode,
        wake_alias=wake_alias,
        wake_probe=wake_probe,
        wake_confirm=wake_confirm,
        wake_reject_reason=wake_reject_reason,
        speaker_user_id=member.id,
        duration_sec=duration_sec,
        room_state=room_state_snapshot(room_session_key),
        apply_post_corrections=apply_stt_post_corrections,
        clean_text=clean_text,
        set_partial_text=lambda key, value: session_partial_stt_text.__setitem__(key, value),
        commit_stable_transcript=commit_stable_transcript,
        build_transcript_result=build_transcript_result,
        speculate_from_committed_stt=speculate_from_committed_stt,
    )
    if final_transcript.was_corrected:
        print(f"[STT CORRECT] raw={text!r} -> corrected={final_transcript.corrected_text!r}")
    text = final_transcript.corrected_text
    committed_text = final_transcript.committed_text
    transcript_result = final_transcript.transcript_result
    if final_transcript.speculative_policy is not None:
        remember_speculative_policy(session_key, final_transcript.speculative_policy)
    if committed_text and len(clean_text(text)) >= len(committed_text):
        text = clean_text(text)
    meta = metrics.setdefault("meta", {})
    interrupted_at_raw = meta.get("tts_interrupted_at")
    interrupted_at = None
    if interrupted_at_raw is not None:
        try:
            interrupted_at = float(interrupted_at_raw)
        except (TypeError, ValueError):
            interrupted_at = None
    if meta.get("tts_interrupted_by_user_audio") or meta.get("local_tts_interrupted_by_user_audio"):
        merged_text, merge_meta = maybe_merge_barge_in_utterance(
            room_last_voice_utterance_for_merge,
            room_session_key=room_session_key,
            session_key=session_key,
            user_id=member.id,
            current_text=transcript_result.final_text,
            current_turn_id=turn_id,
            interrupted_at=interrupted_at,
            merge_window_sec=VOICE_BARGE_IN_MERGE_WINDOW_SEC,
            tts_interrupted_window_sec=VOICE_BARGE_IN_TTS_INTERRUPTED_WINDOW_SEC,
            incomplete_window_sec=VOICE_BARGE_IN_INCOMPLETE_UTTERANCE_WINDOW_SEC,
            complete_question_window_sec=VOICE_BARGE_IN_QUESTION_WINDOW_SEC,
            adaptive_window_enabled=VOICE_BARGE_IN_ADAPTIVE_MERGE_ENABLED,
            clean_text=clean_text,
        )
        if merge_meta:
            transcript_result = replace(
                transcript_result,
                final_text=merged_text,
                committed_text=merged_text,
            )
            text = merged_text
            committed_text = merged_text
            meta["barge_in_utterance_merge"] = merge_meta
            log_voice_stage(
                metrics,
                "TTS barge-in utterance merged",
                extra=f"delta={merge_meta.get('delta_sec')} text={merged_text!r}",
            )
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

    final_wake_decision = decide_final_wake_veto(
        final_text=transcript_result.final_text,
        owner_followup_active=owner_followup_active,
        extract_leading_wake_alias=extract_leading_wake_alias,
    )
    if not final_wake_decision.accepted:
        wake_detected = False
        wake_match_mode = "rejected"
        wake_reject_reason = final_wake_decision.reject_reason or "full_text_veto"
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
        log_voice_stage(metrics, "final text veto", extra=f"wake_reject_reason={wake_reject_reason} text={transcript_result.final_text!r}")
        log_voice_bottleneck_summary(metrics, label="voice_drop", extra="drop=full_text_veto", event_name="voice_drop_summary")
        return
    if final_wake_decision.wake_alias is not None:
        wake_alias = final_wake_decision.wake_alias

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
        room_last_voice_utterance_for_merge=room_last_voice_utterance_for_merge,
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
    mark_startup_component("discord_gateway", "done", clean_text(str(bot.user or "")))
    ensure_voice_worker_started()
    try:
        await start_control_page_server()
    except Exception as e:
        mark_startup_component("control_api", "failed", repr(e))
        print(f"[CONTROL PAGE] start_fail err={e!r}")
    try:
        await ensure_startup_components_ready()
        await ensure_local_mic_service_started()
        ensure_vision_watch_started()
    except Exception as e:
        print(f"[STARTUP] init_fail err={e!r}")
        raise
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


def build_discord_text_message_handler_deps() -> DiscordTextMessageHandlerDeps:
    return DiscordTextMessageHandlerDeps(
        process_commands=bot.process_commands,
        bot_user=bot.user,
        is_thread_parent=lambda parent: isinstance(parent, discord.TextChannel),
        remember_session_followup_target=remember_session_followup_target,
        get_guild_command_prefix=get_guild_command_prefix,
        get_guild_command_only_channel_ids=get_guild_command_only_channel_ids,
        contains_wake_word=contains_wake_word,
        is_session_active_for_user=is_session_active_for_user,
        strip_voice_wake_word=strip_voice_wake_word,
        empty_wake_text="이름만 부름. 친구처럼 짧게 반말로, 원래 하던 일을 잠깐 말하며 자연스럽게 반응해.",
        log_turn_event=log_turn_event,
        current_turn_id=current_turn_id,
        resolve_pending_proactive_question_for_turn=resolve_pending_proactive_question_for_turn,
        session_locks=session_locks,
        reply_slot_locks=reply_slot_locks,
        begin_user_text_turn=begin_user_text_turn,
        replace_room_turn_scope=replace_room_turn_scope,
        attach_current_task=_attach_current_task,
        auto_join_voice=AUTO_JOIN_VOICE,
        ensure_voice_client=ensure_voice_client,
        stream_text_reply=stream_text_reply,
        strip_omnivoice_tags=strip_omnivoice_tags,
        execute_voice_delivery_plan=execute_voice_delivery_plan,
        detach_task=_detach_task,
        clear_room_turn_scope=clear_room_turn_scope,
        session_speculative_policies=session_speculative_policies,
        compute_runtime_mode=compute_runtime_mode,
        record_context_pipeline_benchmark=record_context_pipeline_benchmark,
        schedule_memory_update=schedule_memory_update,
        should_force_search_followup=should_force_search_followup,
        schedule_search_followup=schedule_search_followup,
        session_state_snapshot=session_state_snapshot,
        finish_assistant_text_turn=finish_assistant_text_turn,
        log_voice_bottleneck_summary=log_voice_bottleneck_summary,
        format_display_text=format_display_text,
        log=print,
    )


@bot.event
async def on_message(message: discord.Message):
    await handle_discord_text_message(message, build_discord_text_message_handler_deps())


# =========================================================
# 명령어
# =========================================================
@bot.command(name="들어와", aliases=["join"])
async def join_voice(ctx):
    await handle_join_voice_command(ctx, ensure_listening_voice_client=ensure_listening_voice_client, log=print)


@bot.command(name="다시들어와", aliases=["rejoin"])
async def rejoin_voice(ctx):
    await handle_rejoin_voice_command(ctx, ensure_listening_voice_client=ensure_listening_voice_client, log=print)


@bot.command(name="나가", aliases=["leave"])
async def leave_voice(ctx):
    await handle_leave_voice_command(ctx, mark_manual_disconnect=mark_voice_manual_disconnect)


async def restart_bot_process() -> None:
    await asyncio.sleep(1.0)
    stop_control_page_background_tasks()
    stop_vision_watch_task()
    stop_local_mic_service()
    script_path = Path(__file__).resolve()
    project_dir = script_path.parent
    launch_runtime_restart_sequence(
        project_dir,
        local_only_mode=LOCAL_ONLY_MODE,
        discord_enabled=DISCORD_ENABLED,
        control_page_port=CONTROL_PAGE_PORT,
        fallback_target=project_dir / "evelyn_core" / "start.bat",
    )
    os._exit(0)


def schedule_evelyn_stack_shutdown(delay_ms: int = 3000) -> bool:
    return runtime_schedule_evelyn_stack_shutdown(PROJECT_ROOT, delay_ms=delay_ms)


def schedule_evelyn_local_shutdown(delay_ms: int = 1500) -> bool:
    return runtime_schedule_evelyn_local_shutdown(PROJECT_ROOT, delay_ms=delay_ms)


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


async def run_local_only_mode() -> None:
    print("[LOCAL MODE] starting without Discord gateway")
    mark_startup_component("discord_gateway", "done", "disabled by DISCORD_ENABLED=false")
    try:
        await start_control_page_server()
    except Exception as e:
        mark_startup_component("control_api", "failed", repr(e))
        print(f"[CONTROL PAGE] start_fail err={e!r}")
        raise
    try:
        await ensure_startup_components_ready()
        await ensure_local_mic_service_started()
        ensure_vision_watch_started()
    except Exception as e:
        print(f"[STARTUP] local_init_fail err={e!r}")
    try:
        await ensure_control_page_background_tasks_started()
    except Exception as e:
        print(f"[CONTROL PAGE] bg_tasks_fail err={e!r}")
    print(f"[LOCAL MODE] ready url={control_page_local_url()}")
    await asyncio.Event().wait()


def is_control_command_authorized(ctx) -> bool:
    perms = getattr(ctx.author, "guild_permissions", None)
    return is_control_command_authorized_payload(
        author_id=getattr(ctx.author, "id", None),
        is_administrator=bool(perms and getattr(perms, "administrator", False)),
        allowed_user_ids=ALLOWED_RESTART_USER_IDS,
    )


async def handle_control_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send(control_command_check_failure_message())
        return
    raise error


@bot.command(name="재시작", aliases=["restart"])
@commands.check(is_control_command_authorized)
async def restart_bot_command(ctx):
    await handle_restart_bot_command(ctx, create_task=asyncio.create_task, restart_bot_process=restart_bot_process)


@restart_bot_command.error
async def restart_bot_command_error(ctx, error):
    await handle_control_command_error(ctx, error)


@bot.command(name="종료", aliases=["shutdown", "quit", "exit"])
@commands.check(is_control_command_authorized)
async def shutdown_bot_command(ctx):
    await handle_shutdown_bot_command(
        ctx,
        schedule_stack_shutdown=schedule_evelyn_stack_shutdown,
        create_task=asyncio.create_task,
        shutdown_bot_process=shutdown_bot_process,
    )


@shutdown_bot_command.error
async def shutdown_bot_command_error(ctx, error):
    await handle_control_command_error(ctx, error)


@bot.command(name="상태", aliases=["status"])
async def status_command(ctx):
    await handle_status_command(
        ctx,
        build_reply=build_status_command_text,
        model_name=MODEL_NAME,
        router_model_name=ROUTER_MODEL_NAME,
        summary_model_name=SUMMARY_MODEL_NAME,
        stt_model_name=STT_MODEL_NAME,
        voice_debug_save_audio=VOICE_DEBUG_SAVE_AUDIO,
        vad_enabled=VAD_ENABLED,
        vad_provider=VAD_PROVIDER,
    )


@bot.command(name="이블린페이지", aliases=["page", "homepage", "website", "landing"])
async def evelyn_page_command(ctx):
    await handle_evelyn_page_command(ctx, resolve_page_url=resolve_evelyn_page_url)


@bot.command(name="접두사", aliases=["prefix"])
@commands.check(is_control_command_authorized)
async def set_guild_prefix(ctx, new_prefix: str | None = None):
    await handle_prefix_command(
        ctx,
        new_prefix,
        default_prefix=DEFAULT_COMMAND_PREFIX,
        get_guild_command_prefix=get_guild_command_prefix,
        save_guild_command_prefix=save_guild_command_prefix,
        build_current_reply=build_prefix_current_reply,
        build_reset_reply=build_prefix_reset_reply,
        build_saved_reply=build_prefix_saved_reply,
        guild_only_message=guild_only_command_message,
    )


@set_guild_prefix.error
async def set_guild_prefix_error(ctx, error):
    await handle_control_command_error(ctx, error)


@bot.command(name="자율시작", aliases=["autonomy-on"])
async def autonomy_start_command(ctx):
    await handle_autonomy_start_command(
        ctx,
        get_or_create_autonomy_engine=get_or_create_autonomy_engine,
        guild_only_message=guild_only_command_message,
    )


@bot.command(name="자율정지", aliases=["autonomy-off"])
async def autonomy_stop_command(ctx):
    await handle_autonomy_stop_command(
        ctx,
        autonomy_engines=autonomy_engines,
        guild_only_message=guild_only_command_message,
    )


@bot.command(name="자율상태", aliases=["autonomy-status"])
async def autonomy_status_command(ctx):
    await handle_autonomy_status_command(
        ctx,
        autonomy_engines=autonomy_engines,
        get_routed_autonomy_executor=get_routed_autonomy_executor,
        build_reply=build_autonomy_status_command_text,
        guild_only_message=guild_only_command_message,
    )


def _mark_text_session_from_command(ctx, user_text: str, answer_text: str, *, awaiting_user_reply: bool = False) -> None:
    if ctx.guild is None:
        return
    thread_id = resolve_text_thread_id(ctx.channel, is_thread_parent=lambda parent: isinstance(parent, discord.TextChannel))
    session_key = make_text_session_key(ctx.guild.id, ctx.channel.id, ctx.author.id, thread_id=thread_id)
    session_state_store.record_command_assistant_turn(
        session_key,
        user_text,
        answer_text,
        system_prompt=SYSTEM_PROMPT,
        max_history_items=MAX_HISTORY,
        guild_id=ctx.guild.id,
        user_id=ctx.author.id,
        channel_id=ctx.channel.id,
        message_id=getattr(ctx.message, "id", None),
        awaiting_user_reply=awaiting_user_reply,
        normal_ttl_sec=ACTIVE_CONVERSATION_TEXT_SEC,
        question_ttl_sec=ACTIVE_CONVERSATION_TEXT_QUESTION_SEC,
    )


@bot.command(name="마크접속", aliases=["mc-connect", "minecraft-connect"])
async def minecraft_connect_command(ctx):
    await handle_minecraft_connect_command(
        ctx,
        enable_minecraft_mode=enable_minecraft_mode,
        build_reply=build_minecraft_connect_reply,
        mark_text_session_from_command=_mark_text_session_from_command,
        guild_only_message=guild_only_command_message,
    )


@bot.command(name="마크종료", aliases=["mc-disconnect", "minecraft-disconnect"])
async def minecraft_disconnect_command(ctx):
    await handle_minecraft_disconnect_command(
        ctx,
        disable_minecraft_mode=disable_minecraft_mode,
        mark_text_session_from_command=_mark_text_session_from_command,
        guild_only_message=guild_only_command_message,
    )


@bot.command(name="마크상태", aliases=["mc-status", "minecraft-status"])
async def minecraft_status_command(ctx):
    await handle_minecraft_status_command(
        ctx,
        get_minecraft_client=get_minecraft_client,
        build_reply=build_minecraft_status_command_text,
        mark_text_session_from_command=_mark_text_session_from_command,
        guild_only_message=guild_only_command_message,
    )


@bot.command(name="마크목표", aliases=["mc-goal", "minecraft-goal"])
async def minecraft_goal_command(ctx, *, goal: str | None = None):
    await handle_minecraft_goal_command(
        ctx,
        goal=goal,
        get_minecraft_client=get_minecraft_client,
        build_missing_reply=build_minecraft_goal_missing_reply,
        build_updated_reply=build_minecraft_goal_updated_reply,
        mark_text_session_from_command=_mark_text_session_from_command,
        guild_only_message=guild_only_command_message,
    )



@bot.command(name="관찰채널", aliases=["observe-channel"])
@commands.check(is_control_command_authorized)
async def observe_channel_command(ctx, action: str | None = None, channel: discord.TextChannel | None = None):
    await handle_channel_setting_command(
        ctx,
        action,
        channel,
        setting_key="observe_channel_ids",
        label="👀 관찰채널",
        add_success="✅ 관찰채널에 {channel.mention} 추가했어. (총 {count}개)",
        remove_success="🗑️ 관찰채널에서 {channel.mention} 뺐어. (총 {count}개)",
        normalize_action=normalize_channel_setting_action,
        get_channel_ids=get_guild_observe_channel_ids,
        add_channel_setting=add_guild_channel_setting,
        remove_channel_setting=remove_guild_channel_setting,
        get_guild_command_prefix=get_guild_command_prefix,
        build_list_reply=build_channel_setting_list_reply,
        build_usage_reply=build_observe_channel_usage,
        guild_only_message=guild_only_command_message,
    )


@observe_channel_command.error
async def observe_channel_command_error(ctx, error):
    await handle_control_command_error(ctx, error)


@bot.command(name="명령채널", aliases=["command-channel"])
@commands.check(is_control_command_authorized)
async def command_channel_command(ctx, action: str | None = None, channel: discord.TextChannel | None = None):
    await handle_channel_setting_command(
        ctx,
        action,
        channel,
        setting_key="command_only_channel_ids",
        label="🧭 명령채널",
        add_success="✅ 명령채널에 {channel.mention} 추가했어. 이제 여기선 명령어만 읽어.",
        remove_success="🗑️ 명령채널에서 {channel.mention} 뺐어. (총 {count}개)",
        normalize_action=normalize_channel_setting_action,
        get_channel_ids=get_guild_command_only_channel_ids,
        add_channel_setting=add_guild_channel_setting,
        remove_channel_setting=remove_guild_channel_setting,
        get_guild_command_prefix=get_guild_command_prefix,
        build_list_reply=build_channel_setting_list_reply,
        build_usage_reply=build_command_channel_usage,
        guild_only_message=guild_only_command_message,
    )


@command_channel_command.error
async def command_channel_command_error(ctx, error):
    await handle_control_command_error(ctx, error)


@bot.command(name="도움말", aliases=["help"])
async def help_command(ctx):
    prefix = get_guild_command_prefix(ctx.guild.id if ctx.guild else None)
    await ctx.send(build_help_command_text(prefix=prefix, control_authorized=is_control_command_authorized(ctx)))


@bot.command(name="초기화", aliases=["reset"])
@commands.check(is_control_command_authorized)
async def reset_guild_memory(ctx):
    await handle_reset_guild_memory_command(
        ctx,
        memory_root=MEMORY_ROOT,
        reset_guild_runtime_state=reset_guild_runtime_state,
        remove_tree=shutil.rmtree,
        get_guild_command_prefix=get_guild_command_prefix,
        build_reply=build_reset_guild_memory_reply,
        guild_only_message=guild_only_command_message,
    )


@reset_guild_memory.error
async def reset_guild_memory_error(ctx, error):
    await handle_control_command_error(ctx, error)


# =========================================================
# 실행
# =========================================================
if DISCORD_ENABLED and not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN 환경변수가 설정되지 않았습니다.")

acquire_instance_lock()
if DISCORD_ENABLED:
    bot.run(DISCORD_BOT_TOKEN)
else:
    try:
        asyncio.run(run_local_only_mode())
    except KeyboardInterrupt:
        pass
