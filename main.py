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
from dataclasses import dataclass
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
    pick_recent_user_text,
)
from evelyn_core.autonomy_router import (
    ResolveRouteExecutorRuntimeDeps,
    RoutedAutonomyExecutor,
)
from evelyn_core.autonomy_runtime_factory import (
    AutonomyRuntimeFactoryDeps,
    get_or_create_autonomy_engine_from_runtime,
)
from evelyn_core.config import *
from evelyn_core.instance_lock_runtime import (
    acquire_instance_lock_from_main,
    release_instance_lock_from_main,
)
from evelyn_core.guild_runtime_reset import GuildRuntimeResetDeps, reset_guild_runtime_state_from_runtime
from evelyn_core.guild_runtime_reset import build_guild_runtime_reset_deps as build_guild_runtime_reset_deps_from_runtime
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
from evelyn_core.minecraft_live_state_runtime import (
    ControlPageMinecraftLiveSnapshotRuntimeDeps,
    MinecraftLiveObservationRuntimeDeps,
    observe_live_minecraft_state_from_runtime,
)
from evelyn_core.question_shaping import (
    enforce_question_limits,
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
from evelyn_core.cognitive_followup_policy import (
    ShouldForceSearchFollowupRuntimeDeps,
    should_force_search_followup_from_runtime,
)
from evelyn_core.cognitive_state_runtime import CognitiveStateRuntimeDeps
from evelyn_core.cognitive_refresh_composition import (
    CognitiveRefreshComposition,
    CognitiveRefreshCompositionDeps,
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
from evelyn_core.vision_runtime import (
    LiveVisionContextRuntimeDeps,
    VisionRuntimeDeps,
    build_live_vision_context_from_runtime,
    build_vision_observation_prompt_from_runtime,
    build_vision_watch_prompt_from_runtime,
    format_vision_observation_from_runtime,
    vision_watch_scene_looks_bad_from_runtime,
)
from evelyn_core.vision_watch_composition import (
    VisionWatchComposition,
    VisionWatchCompositionDeps,
)
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
    new_turn_id as new_session_turn_id,
)
from evelyn_core.session_turn_runtime import (
    SessionTurnRuntimeDeps,
)
from evelyn_core.room_speaker_activity import RoomSpeakerActivityStore
from evelyn_core.response_output_policy import (
    ResponseOutputPolicyRuntimeDeps,
    extract_json_object_from_runtime,
    fallback_for_unrequested_minecraft_leak_from_runtime,
    format_display_text_from_runtime,
    parse_response_action_tag,
    sanitize_unrequested_minecraft_leak_from_runtime,
    should_label_question_response_from_runtime,
)
from evelyn_core.search_followup_policy import (
    answer_promises_search,
    strip_search_answer_sources,
)
from evelyn_core.search_tools import search_duckduckgo as search_duckduckgo_payload
from evelyn_core.runtime_status_context import (
    RuntimeStatusContextDeps,
    RuntimeStatusContextState,
    answer_gpu_runtime_status_query,
    build_runtime_status_context_from_runtime,
    load_runtime_gpu_status,
    load_runtime_recent_errors,
    probe_runtime_tcp_service,
)
from evelyn_core.runtime_mode_policy import compute_runtime_mode_from_state, apply_runtime_mode_policy
from evelyn_core.route_fallback_policy import (
    classify_llm_route_fallback,
)
from evelyn_core.llm_route_runtime import LlmRouteRuntimeDeps
from evelyn_core.json_llm_request_runtime import JsonLlmRequestRuntimeDeps
from evelyn_core.fast_path_policy import (
    FastPathPolicyRuntimeDeps,
)
from evelyn_core.tool_awareness_policy import build_tool_awareness_context
from evelyn_core.local_tool_diagnostic_context import build_local_tool_diagnostic_context
from evelyn_core.http_session_runtime import ensure_http_session_from_runtime
from evelyn_core.llm_context_assembly import LlmContextAssemblyDeps
from evelyn_core.llm_warmup_runtime import LlmWarmupRuntimeDeps
from evelyn_core.llm_route_composition_runtime import LlmRouteComposition, LlmRouteCompositionDeps
from evelyn_core.voice_io_composition_runtime import VoiceIoComposition, VoiceIoCompositionDeps
from evelyn_core.voice_support_composition_runtime import (
    VoiceSupportComposition,
    VoiceSupportCompositionDeps,
)
from evelyn_core.voice_runtime_composition_runtime import (
    LocalMicCompositionDeps,
    VoiceDebugCompositionDeps,
    VoicePipelineCompositionDeps,
    VoiceRuntimeComposition,
    VoiceRuntimeCompositionDeps,
)
from evelyn_core.conversation_session_composition import (
    ConversationSessionComposition,
    ConversationSessionCompositionDeps,
)
from evelyn_core.conversation_observability_composition import (
    ConversationObservabilityComposition,
    ConversationObservabilityCompositionDeps,
)
from evelyn_core.runtime_lifecycle_composition import (
    RuntimeLifecycleComposition,
    RuntimeLifecycleCompositionDeps,
    RuntimeProcessCompositionDeps,
    RuntimeStartupCompositionDeps,
)
from evelyn_core.discord_app_composition_runtime import (
    DiscordAppComposition,
    DiscordAppCompositionDeps,
    DiscordCommandCompositionDeps,
    DiscordEventCompositionDeps,
)
from evelyn_core.main_llm_runtime import (
    AskLlmOnceRuntimeDeps,
    MainLlmRuntimeDeps,
)
from evelyn_core.search_followup_runtime import (
    SearchFollowupRuntimeDeps,
)
from evelyn_core.search_answer_runtime import (
    SearchAnswerRuntimeDeps,
)
from evelyn_core.memory_context_state import build_memory_context
from evelyn_core.startup_audio_runtime import (
    OpusStartupRuntimeDeps,
    SttWarmupRuntimeDeps,
)
from evelyn_core.startup_component_state import (
    STARTUP_BOOT_STEPS,
)
from evelyn_core.stt_transcription_runtime import SttTranscriptionRuntimeDeps
from evelyn_core.stt_text_runtime import build_stt_text_runtime_deps
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
from evelyn_core.memory_update_runtime import MemoryUpdateRuntimeDeps
from evelyn_core.memory_maintenance_composition import (
    MemoryMaintenanceComposition,
    MemoryMaintenanceCompositionDeps,
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
from evelyn_core.discord_tts_stream_runtime import (
    DiscordTtsSingleRuntimeDeps,
    DiscordTtsStreamRuntimeDeps,
)
from evelyn_core.discord_voice_connection_runtime import DiscordVoiceConnectionRuntimeDeps
from evelyn_core.discord_settings_runtime import (
    DiscordSettingsRuntimeDeps,
    build_discord_settings_runtime_deps as build_discord_settings_runtime_deps_from_main,
    resolve_command_prefix_from_runtime,
    add_guild_channel_setting_from_runtime,
    get_guild_command_only_channel_ids_from_runtime,
    get_guild_command_prefix_from_runtime,
    get_guild_observe_channel_ids_from_runtime,
    normalize_command_prefix_from_runtime,
    remove_guild_channel_setting_from_runtime,
    save_guild_channel_list_from_runtime,
    save_guild_command_prefix_from_runtime,
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
    guild_only_command_message,
    normalize_channel_setting_action,
)
from evelyn_core.discord_command_handlers import make_control_command_authorized_checker
from evelyn_core.discord_command_session_runtime import DiscordCommandSessionRuntimeDeps
from evelyn_core.discord_ingress import (
    build_voice_ingress_context,
    resolve_text_thread_id,
    normalize_voice_debug_meta,
    voice_ingress_source,
)
from evelyn_core.discord_text_turn import DiscordTextMessageHandlerDeps
from evelyn_core.session_key_runtime import (
    make_person_memory_key,
    make_room_memory_key,
    make_session_memory_key,
    make_text_reply_slot_key,
    make_text_session_key,
    make_voice_room_session_key,
    make_voice_session_key,
    runtime_session_key,
)
from evelyn_core.discord_text_reply_runtime import DiscordTextReplyRuntimeDeps
from evelyn_core.discord_session_policy import (
    estimate_voice_like_probability_policy,
    is_transport_corrupted_audio_policy,
    should_interrupt_tts,
)
from evelyn_core.discord_session_policy_runtime import (
    DiscordSessionPolicyRuntimeDeps,
    is_short_followup_candidate_from_runtime,
    is_tail_fragment_candidate_from_runtime,
    is_transport_corrupted_audio_from_runtime,
    should_ignore_short_transcription_from_runtime,
    should_require_confirm_exact_for_wake_from_runtime,
    should_skip_full_stt_after_wake_probe_from_runtime,
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
    normalize_voice_input_mode,
)
from evelyn_core.local_runtime_context import build_evelyn_runtime_dependency_context_from_payload
from evelyn_core.local_control_tts_runtime import (
    build_local_control_tts_runtime_deps,
    schedule_local_control_tts_from_runtime,
)
from evelyn_core.tts_warmup_runtime import TtsWarmupRuntimeDeps
from evelyn_core.tts_interrupt_runtime import (
    TtsInterruptRuntimeDeps,
    VoiceTtsInterruptGateDeps,
    run_voice_tts_interrupt_gate_from_runtime,
)
from evelyn_core.voice_timing_runtime import (
    build_voice_timing_runtime_deps as build_voice_timing_runtime_deps_from_runtime,
    VoiceTimingRuntimeDeps,
)
from evelyn_core.local_tts_playback import LocalTtsPlaybackManager
from evelyn_core.local_tts_stream_runtime import (
    LocalTtsSingleRuntimeDeps,
    LocalTtsStreamRuntimeDeps,
)
from evelyn_core.observability_metrics import (
    ModelCallMetricsStore,
    record_turn_stage_metric,
    summarize_voice_p95_metrics,
)
from evelyn_core.omnivoice_request_runtime import (
    OmniVoiceRequestRuntimeDeps,
)
from evelyn_core.omnivoice_source_runtime import OmniVoiceSourceRuntimeDeps
from evelyn_core.page_urls import (
    build_evelyn_page_url_runtime_deps,
    resolve_evelyn_page_url_from_runtime,
)
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
from evelyn_core.question_policy_runtime import (
    QuestionPolicyRuntimeDeps,
    QuestionPolicyStateRuntimeDeps,
)
from evelyn_core.assistant_contracts import (
    TtsSynthRequest,
    TtsSynthResult,
)
from evelyn_core.assistant_prompt_contract import build_evelyn_system_prompt
from evelyn_core.cached_tts_runtime import CachedTtsRuntimeDeps
from evelyn_core.stt_model_runtime import (
    SttModelRuntimeDeps,
    build_stt_model_runtime_deps as build_stt_model_runtime_deps_from_runtime,
    get_stt_model_from_runtime,
    normalize_stt_language_from_runtime,
)
from evelyn_core.control_page_contracts import memory_panel_reply
from evelyn_core.control_page_http import (
    control_page_cors_middleware,
)
from evelyn_core.control_page_server import open_path_with_system, open_url_with_system
from evelyn_core.control_page_server_start_runtime import (
    ControlPageServerStartRuntimeDeps,
)
from evelyn_core.control_page_composition_runtime import (
    ControlPageComposition,
    ControlPageCompositionDeps,
    ControlPageHttpComposition,
    ControlPageHttpCompositionDeps,
)
from evelyn_core.control_page_state import (
    ControlPageChatLogStore,
    ControlPageMinecraftSnapshotCache,
    ControlPageRuntimeServicesCache,
    ControlPageUiCommandStore,
    build_control_page_autonomy_reply_payload,
    build_control_page_inventory_reply_payload,
    build_control_page_local_status_text_payload,
    build_control_page_minecraft_reply_payload,
    build_control_page_runtime_services_error_payload,
    build_control_page_status_text_payload,
    build_control_page_voice_continuity_reply_payload,
    build_control_page_voice_status_reply_payload,
    command_status,
    control_page_open_memory_vault_tool_reply,
    execute_control_page_memory_tool,
    execute_control_page_minecraft_tool,
    execute_control_page_runtime_tool,
    execute_control_page_voice_tool,
    memory_vault_obsidian_url,
    sanitize_control_page_welcome_text_payload,
)
from evelyn_core.control_page_guild_runtime import (
    ControlPageGuildSelectionRuntimeDeps,
)
from evelyn_core.control_page_state_handler import ControlPageStateDeps, build_control_page_state_from_runtime
from evelyn_core.control_page_runtime_probe import probe_control_page_runtime_services
from evelyn_core.control_page_minecraft_snapshot_runtime import (
    ControlPageBackgroundTasksRuntimeDeps,
    ControlPageMinecraftSnapshotRuntimeDeps,
)
from evelyn_core.control_page_runtime_services_runtime import (
    ControlPageRuntimeServicesRuntimeDeps,
    ControlPageRuntimeServicesProbeDeps,
    probe_control_page_runtime_services_once_from_runtime,
)
from evelyn_core.control_page_status_runtime import (
    ControlPageStatusRuntimeDeps,
)
from evelyn_core.control_page_search_runtime import (
    ControlPageSearchRuntimeDeps,
)
from evelyn_core.control_page_text_runtime import (
    ControlPageTextRuntimeDeps,
)
from evelyn_core.control_page_tool_runtime import (
    ControlPageInputRuntimeDeps,
    ControlPageToolRuntimeDeps,
)
from evelyn_core.control_page_ui_runtime import (
    ControlPageUiRuntimeDeps,
    ControlPageWelcomeRuntimeDeps,
)
from evelyn_core.control_page_tools import (
    build_control_page_all_commands,
    build_control_page_commands,
    build_control_page_help_reply,
    cheap_control_page_tool_decision,
    control_page_tool_decision_from_llm,
    control_page_tool_policy_error,
    control_page_tool_reply_from_execution,
    control_page_tool_registry_prompt,
    should_route_control_page_tool_candidate,
)
from evelyn_core.tts_playback import (
    CachedWaveAudioSource,
    OmniVoicePCMStream,
    StreamingVoiceDelivery,
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
from evelyn_core.voice_stt_flow import (
    apply_fuzzy_wake_near_miss,
    apply_strict_wake_confirm_policy,
    build_final_transcript_flow,
    decide_final_wake_veto,
    get_matching_speculative_policy_from_runtime,
    interpret_wake_probe_result,
    remember_speculative_policy_from_runtime,
    run_full_stt_with_optional_rescore,
    run_partial_stt_flow,
    speculate_from_committed_stt_from_runtime,
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
    VOICE_BARGE_IN_REASON_CODE,
    VOICE_BARGE_IN_REASON_LABEL,
    VoiceBargeInContinuityRuntimeDeps,
    VoiceBargeInContinuityTracker,
)
from evelyn_core.voice_utterance import (
    UtteranceAssemblyConfig,
)
from evelyn_core.voice_orchestration import (
    VoiceTranscriptReplyDeps,
    apply_voice_ingress_dequeue_debug_meta,
    build_rejected_voice_turn,
    build_voice_ingress_item,
    enqueue_voice_ingress_item,
    evaluate_voice_ingress_dequeue,
    process_voice_reply_from_transcript_context,
)
from evelyn_core.voice_turn_entry_runtime import (
    VoiceTurnEntryRuntimeDeps,
)
from evelyn_core.voice_reply_dispatch_runtime import (
    VoiceReplyDispatchDeps,
    dispatch_voice_reply_from_runtime,
)
from evelyn_core.voice_member_audio_pipeline_runtime import VoiceMemberAudioPipelineDeps
from evelyn_core.voice_route_execution import (
    VoiceMainLlmStreamingDeps,
    VoiceRouteExecutionDeps,
    build_voice_main_llm_streaming_deps as build_voice_main_llm_streaming_deps_from_runtime,
)
from evelyn_core.voice_response_runtime import (
    VoiceResponseRuntimeDeps,
    MainResponseGuidanceRuntimeDeps,
    build_main_response_guidance_from_runtime,
)
from evelyn_core.voice_stream_chunks import VoiceStreamChunkDeps
from evelyn_core.voice_ingress_runtime import (
    VoiceIngressEntrypointDeps,
    VoiceIngressRuntimeDeps,
)
from evelyn_core.voice_audio_ingress_runtime import (
    VoiceAudioIngressDeps,
    prepare_voice_audio_ingress_from_runtime,
)
from evelyn_core.voice_wake_probe_runtime import (
    VoiceWakeProbeDeps,
    run_voice_wake_probe_from_runtime,
)
from evelyn_core.voice_stt_execution_runtime import (
    VoiceSttExecutionDeps,
    run_voice_stt_execution_from_runtime,
)
from evelyn_core.voice_transcript_finalize_runtime import (
    VoiceTranscriptFinalizeDeps,
    finalize_voice_transcript_from_runtime,
)
from evelyn_core.voice_session_gate_runtime import (
    VoiceSessionGateDeps,
    run_voice_session_gate_from_runtime,
)
from evelyn_core.voice_reply_side_effects import VoiceReplySideEffectDeps
from evelyn_core.voice_reply_gate_runtime import VoiceReplyGateRuntimeDeps
from evelyn_core.voice_delivery_runtime import VoiceDeliveryRuntimeDeps
from evelyn_core.voice_pipeline import (
    DeliveryPlan,
    RouteDecision,
    TranscriptResult,
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
from evelyn_voice import EvelynVoiceClient


TURN_TRACE_JSON_LOG = os.getenv("TURN_TRACE_JSON_LOG", "true").lower() == "true"
VOICE_CONSOLE_ONLY_STT_AND_REPLY = os.getenv("VOICE_CONSOLE_ONLY_STT_AND_REPLY", "true").lower() == "true"
VOICE_BOTTLENECK_LOGS = os.getenv("VOICE_BOTTLENECK_LOGS", "true").lower() == "true"
VOICE_TRACE_ALL_EVENTS = os.getenv("VOICE_TRACE_ALL_EVENTS", "true").lower() == "true"
TURN_TRACE_LOG_DIR = Path(os.getenv("TURN_TRACE_LOG_DIR", str(PROJECT_ROOT / "logs" / "turn_trace")))
VOICE_DEBUG_SAVE_AUDIO = os.getenv("VOICE_DEBUG_SAVE_AUDIO", "false").lower() == "true"
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


def build_discord_settings_runtime_deps() -> DiscordSettingsRuntimeDeps:
    return build_discord_settings_runtime_deps_from_main(
        default_command_prefix=DEFAULT_COMMAND_PREFIX,
        prefix_cache=guild_prefix_cache,
        now=time.time,
    )


def build_question_policy_runtime_deps() -> QuestionPolicyRuntimeDeps:
    return QuestionPolicyRuntimeDeps(
        normalize_question_policy_mapping_payload=normalize_question_policy_mapping_payload,
        extract_question_policy_from_route_meta_payload=extract_question_policy_from_route_meta_payload,
        user_wants_direct_answer_payload=user_wants_direct_answer_payload,
        user_frustration_with_questions_payload=user_frustration_with_questions_payload,
        is_continuable_technical_topic_payload=is_continuable_technical_topic_payload,
    )


def build_question_policy_state_runtime_deps() -> QuestionPolicyStateRuntimeDeps:
    return QuestionPolicyStateRuntimeDeps(
        question_cooldown_hit_payload=question_policy_state.question_cooldown_hit,
        apply_fast_path_question_policy_payload=question_policy_state.apply_fast_path_policy,
        record_question_trace_payload=question_policy_state.record_question_trace,
        summarize_question_metrics_payload=question_policy_state.summarize_question_metrics,
        proactive_scope_candidates_payload=question_policy_state.proactive_scope_candidates,
        record_session_question_asked_payload=question_policy_state.record_session_question_asked,
        resolve_pending_proactive_question_for_turn_payload=question_policy_state.resolve_pending_proactive_question_for_turn,
        select_and_mark_proactive_question_payload=question_policy_state.select_and_mark_proactive_question,
        maybe_append_proactive_question_payload=question_policy_state.maybe_append_proactive_question,
    )


def build_session_turn_runtime_deps() -> SessionTurnRuntimeDeps:
    return SessionTurnRuntimeDeps(
        session_state_store=session_state_store,
        system_prompt=SYSTEM_PROMPT,
        active_conversation_awaiting_reply_sec=ACTIVE_CONVERSATION_AWAITING_REPLY_SEC,
        active_conversation_text_question_sec=ACTIVE_CONVERSATION_TEXT_QUESTION_SEC,
        active_conversation_text_sec=ACTIVE_CONVERSATION_TEXT_SEC,
        max_history_items=MAX_HISTORY_ITEMS,
        session_topic_ids=session_topic_ids,
        build_topic_id_fn=build_session_topic_id,
        new_turn_id_fn=new_session_turn_id,
    )


def release_instance_lock() -> None:
    global instance_lock_handle
    release_instance_lock_from_main(instance_lock_handle, lock_path=instance_lock_path)
    instance_lock_handle = None


def acquire_instance_lock(wait_sec: float = 15.0, poll_sec: float = 0.25) -> None:
    global instance_lock_handle
    instance_lock_handle = acquire_instance_lock_from_main(
        instance_lock_handle,
        lock_path=instance_lock_path,
        wait_sec=wait_sec,
        poll_sec=poll_sec,
    )


def build_discord_session_policy_runtime_deps() -> DiscordSessionPolicyRuntimeDeps:
    return DiscordSessionPolicyRuntimeDeps(
        session_last_turn_accepted_at_get=lambda session_key: session_last_turn_accepted_at.get(session_key, 0.0),
        monotonic_fn=time.monotonic,
        should_require_confirm_exact_for_wake_payload=should_require_confirm_exact_for_wake_policy,
        is_transport_corrupted_audio_payload=is_transport_corrupted_audio_policy,
        no_wake_max_continue_sec=VOICE_NO_WAKE_MAX_CONTINUE_SEC,
        clean_text=clean_text,
        looks_like_brief_filler_text=looks_like_brief_filler_text,
        looks_like_repetitive_noise_text=looks_like_repetitive_noise_text,
        tail_fragment_window_sec=TAIL_FRAGMENT_WINDOW_SEC,
        tail_fragment_max_raw_sec=TAIL_FRAGMENT_MAX_RAW_SEC,
        tail_fragment_max_voiced_ms=TAIL_FRAGMENT_MAX_VOICED_MS,
        tail_fragment_max_longest_ms=TAIL_FRAGMENT_MAX_LONGEST_MS,
        normalize_voice_text=normalize_voice_text,
        normalized_wake_words=normalized_wake_words,
        min_audio_sec=MIN_AUDIO_SEC,
        min_transcribed_len=MIN_TRANSCRIBED_LEN,
        wake_short_text_keep_len=WAKE_SHORT_TEXT_KEEP_LEN,
        audio_duration_fn=lambda pcm_bytes: len(pcm_bytes or b"") / (RATE * CHANNELS * 2),
    )


atexit.register(release_instance_lock)


def normalize_command_prefix(prefix: str | None) -> str:
    return normalize_command_prefix_from_runtime(
        prefix,
        deps=build_discord_settings_runtime_deps(),
    )


def get_guild_command_prefix(guild_id: int | None) -> str:
    return get_guild_command_prefix_from_runtime(
        guild_id,
        deps=build_discord_settings_runtime_deps(),
    )


def save_guild_command_prefix(guild_id: int, prefix: str) -> str:
    return save_guild_command_prefix_from_runtime(
        guild_id,
        prefix,
        deps=build_discord_settings_runtime_deps(),
    )


def get_guild_observe_channel_ids(guild_id: int | None) -> list[int]:
    return get_guild_observe_channel_ids_from_runtime(
        guild_id,
        deps=build_discord_settings_runtime_deps(),
    )


def get_guild_command_only_channel_ids(guild_id: int | None) -> list[int]:
    return get_guild_command_only_channel_ids_from_runtime(
        guild_id,
        deps=build_discord_settings_runtime_deps(),
    )


def save_guild_channel_list(guild_id: int, key: str, channel_ids: list[int]) -> list[int]:
    return save_guild_channel_list_from_runtime(
        guild_id,
        key,
        channel_ids,
        deps=build_discord_settings_runtime_deps(),
    )


def add_guild_channel_setting(guild_id: int, key: str, channel_id: int) -> list[int]:
    return add_guild_channel_setting_from_runtime(
        guild_id,
        key,
        channel_id,
        deps=build_discord_settings_runtime_deps(),
    )


def remove_guild_channel_setting(guild_id: int, key: str, channel_id: int) -> list[int]:
    return remove_guild_channel_setting_from_runtime(
        guild_id,
        key,
        channel_id,
        deps=build_discord_settings_runtime_deps(),
    )


bot = commands.Bot(
    command_prefix=lambda _bot, message: commands.when_mentioned_or(
        resolve_command_prefix_from_runtime(
            message.guild.id if message.guild else None,
            get_guild_command_prefix=get_guild_command_prefix,
        ),
    )(_bot, message),
    intents=intents,
    help_command=None,
)

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
tts_warmup_started = False
http_session: Optional[aiohttp.ClientSession] = None
startup_component_state: dict[str, dict[str, Any]] = {}
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
runtime_status_context_state = RuntimeStatusContextState()
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
VOICE_BARGE_IN_CONTINUITY_TARGET = 5
recent_skill_dispatches: dict[str, float] = {}
SKILL_DISPATCH_CACHE_TTL_SEC = 300.0
SKILL_DISPATCH_REPEAT_WINDOW_SEC = 5.0
SKILL_DISPATCH_CACHE_MAX = 1024


conversation_session_composition = ConversationSessionComposition(
    ConversationSessionCompositionDeps(
        session=build_session_turn_runtime_deps,
        room_owner_user_ids=room_owner_user_ids,
        room_owner_until=room_owner_until,
        room_reply_in_progress=room_reply_in_progress,
        room_speaker_activity_store=room_speaker_activity_store,
        monotonic=time.monotonic,
        log_event=lambda *args, **kwargs: log_turn_event(*args, **kwargs),
    )
)

new_conversation_history = conversation_session_composition.new_conversation_history
remember_session_followup_target = conversation_session_composition.remember_session_followup_target
build_topic_id = conversation_session_composition.build_topic_id
new_turn_id = conversation_session_composition.new_turn_id
current_turn_id = conversation_session_composition.current_turn_id
next_segment_id = conversation_session_composition.next_segment_id
start_new_turn = conversation_session_composition.start_new_turn
begin_user_text_turn = conversation_session_composition.begin_user_text_turn
finish_assistant_text_turn = conversation_session_composition.finish_assistant_text_turn
session_state_snapshot = conversation_session_composition.session_state_snapshot
discord_room_session_policy = conversation_session_composition.discord_room_session_policy
_clear_room_owner = conversation_session_composition.clear_room_owner
room_state_snapshot = conversation_session_composition.room_state_snapshot
_prune_room_speaker_stats = conversation_session_composition.prune_room_speaker_stats
update_room_speaker_activity = conversation_session_composition.update_room_speaker_activity
pick_active_speaker = conversation_session_composition.pick_active_speaker
is_room_owner_active = conversation_session_composition.is_room_owner_active
set_room_owner = conversation_session_composition.set_room_owner
set_room_reply_in_progress = conversation_session_composition.set_room_reply_in_progress
increment_session_bad_audio = conversation_session_composition.increment_session_bad_audio
reset_session_bad_audio = conversation_session_composition.reset_session_bad_audio
update_session_state = conversation_session_composition.update_session_state
mark_session_active = conversation_session_composition.mark_session_active
is_session_active_for_user = conversation_session_composition.is_session_active_for_user
get_conversation_history = conversation_session_composition.get_conversation_history
trim_history = conversation_session_composition.trim_history
append_history = conversation_session_composition.append_history
recent_assistant_reply_summary = conversation_session_composition.recent_assistant_reply_summary
persona_state_hint_for_turn = conversation_session_composition.persona_state_hint_for_turn

conversation_observability_composition = ConversationObservabilityComposition(
    ConversationObservabilityCompositionDeps(
        question_policy=build_question_policy_runtime_deps,
        question_policy_state=build_question_policy_state_runtime_deps,
        turn_scope_registry=turn_scope_registry,
        turn_stage_metrics=turn_stage_metrics,
        model_call_metrics_store=model_call_metrics_store,
        write_turn_trace_event=write_turn_trace_event,
        turn_trace_json_log=TURN_TRACE_JSON_LOG,
        bottleneck_events=_BOTTLENECK_TURN_TRACE_EVENTS,
        summary_events=TURN_SUMMARY_EVENTS,
        console_only_stt_and_reply=VOICE_CONSOLE_ONLY_STT_AND_REPLY,
        voice_bottleneck_logs=VOICE_BOTTLENECK_LOGS,
        voice_trace_all_events=VOICE_TRACE_ALL_EVENTS,
        turn_trace_log_dir=TURN_TRACE_LOG_DIR,
        turn_trace_file_lock=turn_trace_file_lock,
        original_print=_ORIGINAL_PRINT,
        trace_print=print,
        monotonic=time.monotonic,
        now=time.time,
        benchmark_log_path=CONTEXT_PIPELINE_BENCHMARK_LOG,
        project_root=PROJECT_ROOT,
        log=print,
        record_turn_stage_metric=record_turn_stage_metric,
        summarize_voice_p95_metrics=summarize_voice_p95_metrics,
        get_search_followup_queued_count=lambda: search_followup_queued_count,
        build_rejected_voice_turn=build_rejected_voice_turn,
    )
)

log_turn_event = conversation_observability_composition.log_turn_event
record_model_call_trace = conversation_observability_composition.record_model_call_trace
record_context_pipeline_benchmark = conversation_observability_composition.record_context_pipeline_benchmark
merge_log_event_payload = conversation_observability_composition.merge_log_event_payload
replace_room_turn_scope = conversation_observability_composition.replace_room_turn_scope
get_room_turn_scope = conversation_observability_composition.get_room_turn_scope
_attach_current_task = conversation_observability_composition.attach_current_task
_detach_task = conversation_observability_composition.detach_task
create_turn_scoped_task = conversation_observability_composition.create_turn_scoped_task
clear_room_turn_scope = conversation_observability_composition.clear_room_turn_scope
record_turn_stage = conversation_observability_composition.record_turn_stage
record_model_call_metric = conversation_observability_composition.record_model_call_metric
replay_model_call_metrics_from_turn_trace = (
    conversation_observability_composition.replay_model_call_metrics_from_turn_trace
)
ensure_model_call_metrics_replayed = conversation_observability_composition.ensure_model_call_metrics_replayed
record_turn_path_summary = conversation_observability_composition.record_turn_path_summary
summarize_turn_path_metrics = conversation_observability_composition.summarize_turn_path_metrics
summarize_model_call_metrics = conversation_observability_composition.summarize_model_call_metrics
normalize_question_policy_mapping = conversation_observability_composition.normalize_question_policy_mapping
extract_question_policy_from_route_meta = (
    conversation_observability_composition.extract_question_policy_from_route_meta
)
user_wants_direct_answer = conversation_observability_composition.user_wants_direct_answer
user_frustration_with_questions = conversation_observability_composition.user_frustration_with_questions
is_continuable_technical_topic = conversation_observability_composition.is_continuable_technical_topic
question_cooldown_hit = conversation_observability_composition.question_cooldown_hit
apply_fast_path_question_policy = conversation_observability_composition.apply_fast_path_question_policy
record_question_trace = conversation_observability_composition.record_question_trace
summarize_question_metrics = conversation_observability_composition.summarize_question_metrics
proactive_question_scope_candidates = (
    conversation_observability_composition.proactive_question_scope_candidates
)
record_session_question_asked = conversation_observability_composition.record_session_question_asked
resolve_pending_proactive_question_for_turn = (
    conversation_observability_composition.resolve_pending_proactive_question_for_turn
)
select_and_mark_proactive_question = (
    conversation_observability_composition.select_and_mark_proactive_question
)
maybe_append_proactive_question = conversation_observability_composition.maybe_append_proactive_question
summarize_p95_metrics = conversation_observability_composition.summarize_p95_metrics
new_turn_metrics = conversation_observability_composition.new_turn_metrics
mark_turn_stage = conversation_observability_composition.mark_turn_stage
register_drop_reason = conversation_observability_composition.register_drop_reason

configure_tts_playback_logging(log_turn_event)


# =========================================================
# 유틸
# =========================================================
def build_autonomy_runtime_factory_deps() -> AutonomyRuntimeFactoryDeps:
    return AutonomyRuntimeFactoryDeps(
        autonomy_engines=autonomy_engines,
        get_guild=bot.get_guild,
        get_observe_channel_ids=get_guild_observe_channel_ids,
        get_command_only_channel_ids=get_guild_command_only_channel_ids,
        session_followup_targets=session_followup_targets,
        clean_text=clean_text,
        send_discord_text=send_discord_text,
        question_cooldown_hit=question_cooldown_hit,
        evaluate_proactive_question_gate=evaluate_proactive_question_gate,
        proactive_question_scope_candidates=proactive_question_scope_candidates,
        select_question_to_ask=select_question_to_ask,
        runtime_session_key=runtime_session_key,
        get_conversation_history=get_conversation_history,
        pick_recent_user_text=pick_recent_user_text,
        localtime=time.localtime,
        monotonic=time.monotonic,
        autonomy_last_cognitive_refresh_at=autonomy_last_cognitive_refresh_at,
        autonomy_cognitive_refresh_tasks=autonomy_cognitive_refresh_tasks,
        read_cached_cognitive_state=read_cached_cognitive_state,
        read_vision_watch_state=read_vision_watch_state,
        local_tts_snapshot=local_tts_playback_manager.snapshot,
        serialize_local_mic_runtime_state=serialize_local_mic_runtime_state,
        get_active_session_count=lambda: len(active_session_until),
        get_inflight_llm_requests=lambda: inflight_llm_requests,
        last_autonomy_ping_at=last_autonomy_ping_at,
        answer_promises_search=answer_promises_search,
        append_history=append_history,
        schedule_memory_update=schedule_memory_update,
        mark_session_active=mark_session_active,
        build_topic_id=build_topic_id,
        mark_self_state_assistant_output=mark_self_state_assistant_output,
        select_and_mark_proactive_question=select_and_mark_proactive_question,
        update_cognitive_state=update_cognitive_state,
        autonomy_cognitive_stale_sec=AUTONOMY_COGNITIVE_STALE_SEC,
        autonomy_cognitive_min_interval_sec=AUTONOMY_COGNITIVE_MIN_INTERVAL_SEC,
        autonomy_cognitive_force_refresh_sec=AUTONOMY_COGNITIVE_FORCE_REFRESH_SEC,
        vision_watch_interval_sec=VISION_WATCH_INTERVAL_SEC,
        active_conversation_text_question_sec=ACTIVE_CONVERSATION_TEXT_QUESTION_SEC,
        active_conversation_text_sec=ACTIVE_CONVERSATION_TEXT_SEC,
        autonomy_poll_interval_sec=AUTONOMY_POLL_INTERVAL_SEC,
    )


def get_or_create_autonomy_engine(guild_id: int) -> AutonomyEngine:
    return get_or_create_autonomy_engine_from_runtime(
        guild_id,
        deps=build_autonomy_runtime_factory_deps(),
    )

def build_guild_runtime_reset_deps() -> GuildRuntimeResetDeps:
    return build_guild_runtime_reset_deps_from_runtime(
        session_histories=session_histories,
        session_followup_targets=session_followup_targets,
        active_session_until=active_session_until,
        active_session_user_ids=active_session_user_ids,
        session_last_active_at=session_last_active_at,
        session_awaiting_user_reply=session_awaiting_user_reply,
        session_last_speaker=session_last_speaker,
        session_topic_ids=session_topic_ids,
        session_turn_ids=session_turn_ids,
        session_segment_counters=session_segment_counters,
        session_last_turn_accepted_at=session_last_turn_accepted_at,
        session_last_stt_text=session_last_stt_text,
        room_last_voice_utterance_for_merge=room_last_voice_utterance_for_merge,
        session_partial_stt_text=session_partial_stt_text,
        session_committed_stt_text=session_committed_stt_text,
        session_bad_audio_counts=session_bad_audio_counts,
        room_owner_user_ids=room_owner_user_ids,
        room_owner_until=room_owner_until,
        room_reply_in_progress=room_reply_in_progress,
        room_last_voice_reply_at=room_last_voice_reply_at,
        turn_scope_registry=turn_scope_registry,
        session_locks=session_locks,
        background_search_tasks=background_search_tasks,
        clear_tts_playback_tracking=clear_tts_playback_tracking,
        tts_playback_tracker=tts_playback_tracker,
        memory_locks=memory_locks,
        cognitive_locks=cognitive_locks,
        background_cognitive_tasks=background_cognitive_tasks,
        autonomy_last_cognitive_refresh_at=autonomy_last_cognitive_refresh_at,
        autonomy_cognitive_refresh_tasks=autonomy_cognitive_refresh_tasks,
    )


def reset_guild_runtime_state(guild_id: int) -> None:
    reset_guild_runtime_state_from_runtime(guild_id, deps=build_guild_runtime_reset_deps())


voice_barge_in_continuity_tracker = VoiceBargeInContinuityTracker(
    target_count=VOICE_BARGE_IN_CONTINUITY_TARGET,
    clean_text=clean_text,
    log_enabled=lambda: VOICE_BOTTLENECK_LOGS,
    event_logger=log_turn_event,
)


def build_voice_barge_in_continuity_runtime_deps() -> VoiceBargeInContinuityRuntimeDeps:
    return VoiceBargeInContinuityRuntimeDeps(
        tracker=voice_barge_in_continuity_tracker,
        command_status=command_status,
    )


def compute_runtime_mode(metrics: dict | None) -> str:
    return compute_runtime_mode_from_state(
        metrics,
        tts_backlog=tracked_tts_playback_count(tts_playback_tracker),
        inflight_llm_requests=inflight_llm_requests,
    )


def apply_runtime_mode(mode: str, opts: dict[str, Any] | None = None) -> dict[str, Any]:
    return apply_runtime_mode_policy(mode, opts)


def estimate_voice_like_probability(*, voiced_ms: float, audio_sec: float, body_rms: float) -> float:
    return estimate_voice_like_probability_policy(
        voiced_ms=voiced_ms,
        audio_sec=audio_sec,
        body_rms=body_rms,
        body_rms_min=VOICE_WAVEFORM_BODY_RMS_MIN,
    )


def build_voice_reply_side_effect_deps() -> VoiceReplySideEffectDeps:
    return VoiceReplySideEffectDeps(
        session_speculative_policies=session_speculative_policies,
        append_history=append_history,
        compute_runtime_mode=compute_runtime_mode,
        record_context_pipeline_benchmark=record_context_pipeline_benchmark,
        schedule_memory_update=schedule_memory_update,
        read_cached_cognitive_state=read_cached_cognitive_state,
        apply_ask_gating=apply_ask_gating,
        schedule_search_followup=schedule_search_followup,
        session_state_snapshot=session_state_snapshot,
        mark_session_active=mark_session_active,
        set_room_owner=set_room_owner,
        active_conversation_voice_question_sec=ACTIVE_CONVERSATION_VOICE_QUESTION_SEC,
        active_conversation_voice_sec=ACTIVE_CONVERSATION_VOICE_SEC,
        active_conversation_awaiting_reply_sec=ACTIVE_CONVERSATION_AWAITING_REPLY_SEC,
    )


def build_fast_path_policy_runtime_deps() -> FastPathPolicyRuntimeDeps:
    return FastPathPolicyRuntimeDeps(
        clean_text=clean_text,
        normalize_voice_text=normalize_voice_text,
        should_force_search_query=should_force_search_query,
        control_page_source_aliases=("control_page", "control-page", "local_control_page"),
        control_page_light_request_max_chars=180,
        fast_path_continue_markers=(
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
        ),
        fast_path_directive_markers=(
            "해줘",
            "말해줘",
            "알려줘",
            "정리해줘",
            "요약해줘",
            "설명해줘",
            "번역해줘",
            "고쳐줘",
            "수정해줘",
        ),
        fast_path_deep_route_markers=(
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
        ),
        fast_path_negated_search_markers=(
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
        ),
        fast_path_search_markers=("검색", "찾아", "최신", "뉴스", "시세", "가격", "환율"),
        fast_path_search_route_markers=("검색", "찾아봐", "찾아 봐", "찾아"),
    )


def should_ignore_short_transcription(
    text: str,
    pcm_bytes: bytes,
    *,
    wake_detected: bool = False,
) -> bool:
    return should_ignore_short_transcription_from_runtime(
        text=text,
        pcm_bytes=pcm_bytes,
        wake_detected=wake_detected,
        deps=build_discord_session_policy_runtime_deps(),
    )


def is_short_followup_candidate(
    text: str,
    pcm_bytes: bytes,
    *,
    wake_detected: bool = False,
    owner_followup_active: bool = False,
) -> bool:
    return is_short_followup_candidate_from_runtime(
        text=text,
        pcm_bytes=pcm_bytes,
        wake_detected=wake_detected,
        owner_followup_active=owner_followup_active,
        deps=build_discord_session_policy_runtime_deps(),
    )


def build_voice_reply_gate_runtime_deps() -> VoiceReplyGateRuntimeDeps:
    return VoiceReplyGateRuntimeDeps(
        session_state_snapshot=session_state_snapshot,
        room_state_snapshot=room_state_snapshot,
        is_room_owner_active=is_room_owner_active,
        is_session_active_for_user=is_session_active_for_user,
        tts_input_suppression_reason=tts_playback_manager.input_suppression_reason,
        room_last_voice_reply_at=room_last_voice_reply_at,
        post_tts_ignore_sec=POST_TTS_IGNORE_SEC,
        reply_cooldown_sec=REPLY_COOLDOWN_SEC,
        normalize_voice_text=normalize_voice_text,
        contains_wake_word=contains_wake_word,
        looks_like_brief_filler_text=looks_like_brief_filler_text,
        looks_like_repetitive_noise_text=looks_like_repetitive_noise_text,
        is_similar=is_similar,
        min_text_len=MIN_TEXT_LEN,
        monotonic=time.monotonic,
    )


def should_skip_full_stt_after_wake_probe(*, wake_detected: bool, wake_probe: str, duration_sec: float) -> bool:
    return should_skip_full_stt_after_wake_probe_from_runtime(
        wake_detected=wake_detected,
        wake_probe=wake_probe,
        duration_sec=duration_sec,
        deps=build_discord_session_policy_runtime_deps(),
    )


def should_require_confirm_exact_for_wake(debug_meta: dict | None) -> bool:
    return should_require_confirm_exact_for_wake_from_runtime(debug_meta=debug_meta, deps=build_discord_session_policy_runtime_deps())


def is_transport_corrupted_audio(debug_meta: dict | None) -> bool:
    return is_transport_corrupted_audio_from_runtime(debug_meta=debug_meta, deps=build_discord_session_policy_runtime_deps())


def is_tail_fragment_candidate(
    *,
    session_key: str | None,
    raw_seconds: float,
    voiced_ms: float,
    longest_voiced_ms: float,
    unstable: bool,
) -> bool:
    return is_tail_fragment_candidate_from_runtime(
        session_key=session_key,
        raw_seconds=raw_seconds,
        voiced_ms=voiced_ms,
        longest_voiced_ms=longest_voiced_ms,
        unstable=unstable,
        deps=build_discord_session_policy_runtime_deps(),
    )


def build_voice_ingress_runtime_deps() -> VoiceIngressRuntimeDeps:
    return VoiceIngressRuntimeDeps(
        voice_ingress_queue=voice_ingress_queue,
        voice_utterance_buffers=voice_utterance_buffers,
        voice_utterance_flush_tasks=voice_utterance_flush_tasks,
        voice_utterance_assembly_config=voice_utterance_assembly_config,
        voice_ingress_max_age_sec=VOICE_INGRESS_MAX_AGE_SEC,
        voice_ingress_drop_oldest_on_full=VOICE_INGRESS_DROP_OLDEST_ON_FULL,
        voice_ingress_queue_max=VOICE_INGRESS_QUEUE_MAX,
        evaluate_voice_ingress_dequeue=evaluate_voice_ingress_dequeue,
        apply_voice_ingress_dequeue_debug_meta=apply_voice_ingress_dequeue_debug_meta,
        enqueue_voice_ingress_item=enqueue_voice_ingress_item,
        increment_voice_pipeline_counter=increment_voice_pipeline_counter,
        process_member_audio=_process_member_audio_impl,
        create_task=asyncio.create_task,
        log=print,
    )


def build_voice_ingress_entrypoint_deps() -> VoiceIngressEntrypointDeps:
    return VoiceIngressEntrypointDeps(
        ensure_startup_components_ready=ensure_startup_components_ready,
        normalize_voice_debug_meta=normalize_voice_debug_meta,
        voice_ingress_source=voice_ingress_source,
        should_drop_discord_audio_for_local_mic=should_drop_discord_audio_for_local_mic,
        ensure_voice_worker_started=ensure_voice_worker_started,
        build_voice_ingress_context=build_voice_ingress_context,
        next_segment_id=next_segment_id,
        new_turn_id=new_turn_id,
        room_state_snapshot=room_state_snapshot,
        build_voice_ingress_item=build_voice_ingress_item,
        voice_ingress_queue_depth=voice_ingress_queue.qsize,
        schedule_voice_utterance_item=_schedule_voice_utterance_item,
        monotonic=time.monotonic,
    )


def ensure_voice_worker_started() -> None:
    global voice_worker_task
    ensure_debug_write_worker_started()
    if voice_worker_task is not None and not voice_worker_task.done():
        return
    voice_worker_task = asyncio.create_task(voice_ingress_worker())


def should_label_question_response(text: str, *, session_key: str | None = None) -> bool:
    return should_label_question_response_from_runtime(
        text,
        session_key=session_key,
        deps=build_response_output_policy_runtime_deps(),
    )


def build_response_output_policy_runtime_deps() -> ResponseOutputPolicyRuntimeDeps:
    return ResponseOutputPolicyRuntimeDeps(
        session_state_snapshot_fn=session_state_snapshot,
        answer_gpu_status_answer_fn=answer_gpu_runtime_status_query,
        model_output_stop_tokens=tuple(MAIN_LLM_STOP_TOKENS),
        sanitize_model_output_cleanup_fn=cleanup_assistant_display_artifacts,
    )


def fallback_for_unrequested_minecraft_leak(user_text: str) -> str:
    return fallback_for_unrequested_minecraft_leak_from_runtime(
        user_text,
        deps=build_response_output_policy_runtime_deps(),
    )


def sanitize_unrequested_minecraft_leak(user_text: str, answer: str) -> str:
    return sanitize_unrequested_minecraft_leak_from_runtime(
        user_text,
        answer,
        deps=build_response_output_policy_runtime_deps(),
    )


def format_display_text(text: str, *, session_key: str | None = None) -> str:
    return format_display_text_from_runtime(
        text,
        session_key=session_key,
        deps=build_response_output_policy_runtime_deps(),
    )


def speculate_from_committed_stt(committed_text: str, room_state: dict | None) -> dict | None:
    return speculate_from_committed_stt_from_runtime(
        committed_text,
        room_state,
        clean_text=clean_text,
        fast_path_policy=fast_path_policy,
        monotonic=time.monotonic,
    )


def remember_speculative_policy(session_key: str | None, speculative: dict | None) -> None:
    remember_speculative_policy_from_runtime(session_speculative_policies, session_key, speculative)


def get_matching_speculative_policy(session_key: str | None, user_text: str) -> dict | None:
    return get_matching_speculative_policy_from_runtime(
        session_speculative_policies,
        session_key,
        user_text,
        clean_text=clean_text,
        is_similar=is_similar,
        monotonic=time.monotonic,
    )


def should_force_search_followup(
    guild_id: int | None,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str,
) -> bool:
    return should_force_search_followup_from_runtime(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        deps=build_cognitive_followup_runtime_deps(),
    )


def build_cognitive_followup_runtime_deps() -> ShouldForceSearchFollowupRuntimeDeps:
    return ShouldForceSearchFollowupRuntimeDeps(
        read_cached_cognitive_state_fn=read_cached_cognitive_state,
        apply_ask_gating_fn=apply_ask_gating,
        clean_text_fn=clean_text,
    )


def build_runtime_status_context_deps() -> RuntimeStatusContextDeps:
    return RuntimeStatusContextDeps(
        enabled=RUNTIME_STATUS_CONTEXT_ENABLED,
        refresh_sec=RUNTIME_STATUS_CONTEXT_REFRESH_SEC,
        control_page_host=CONTROL_PAGE_HOST,
        control_page_port=CONTROL_PAGE_PORT,
        llm_server_url=LLM_SERVER_URL,
        router_llm_url=ROUTER_LLM_URL,
        summary_llm_url=SUMMARY_LLM_URL,
        omnivoice_server_url=OMNIVOICE_SERVER_URL,
        minecraft_autonomy_service_port=MINECRAFT_AUTONOMY_SERVICE_PORT,
        voyager_action_backend=VOYAGER_ACTION_BACKEND,
        voyager_codex_gateway_port=VOYAGER_CODEX_GATEWAY_PORT,
        get_control_page_runtime_services=get_control_page_runtime_services,
        is_control_api_ready_from_runtime_services=is_control_api_ready_from_runtime_services,
        probe_runtime_tcp_service=probe_runtime_tcp_service,
        load_runtime_gpu_status=load_runtime_gpu_status,
        load_runtime_recent_errors=load_runtime_recent_errors,
        now=time.time,
    )


async def build_runtime_status_context(*, force: bool = False) -> str:
    return await build_runtime_status_context_from_runtime(
        deps=build_runtime_status_context_deps(),
        state=runtime_status_context_state,
        force=force,
    )


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
    return build_main_response_guidance_from_runtime(
        cognitive_state,
        source=source,
        user_text=user_text,
        session_key=session_key,
        guild_id=guild_id,
        minecraft_state=minecraft_state,
        runtime_status_context=runtime_status_context,
        route_decision=route_decision,
        deps=build_main_response_guidance_runtime_deps(),
    )


def build_main_response_guidance_runtime_deps() -> MainResponseGuidanceRuntimeDeps:
    return MainResponseGuidanceRuntimeDeps(
        clean_text=clean_text,
        apply_ask_gating=apply_ask_gating,
        persona_state_hint_for_turn=persona_state_hint_for_turn,
        recent_assistant_reply_summary=recent_assistant_reply_summary,
        build_tool_awareness_context=build_tool_awareness_context,
        route_available=_skill_route_available,
        format_minecraft_state_summary=format_minecraft_state_summary,
        question_feature_enabled=QUESTION_FEATURE_ENABLED,
    )


def build_vision_watch_runtime_deps() -> VisionRuntimeDeps:
    return VisionRuntimeDeps(
        clean_text=clean_text,
        build_vision_quality=build_vision_quality,
        vision_watch_scene_is_unreliable=vision_watch_scene_is_unreliable,
    )


def build_vision_observation_prompt(user_text: str) -> str:
    return build_vision_observation_prompt_from_runtime(user_text, deps=build_vision_watch_runtime_deps())


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
    return format_vision_observation_from_runtime(
        image_path=image_path,
        image_size=image_size,
        data=data,
        image_deleted=image_deleted,
        deps=build_vision_watch_runtime_deps(),
    )


def build_live_vision_context_runtime_deps() -> LiveVisionContextRuntimeDeps:
    return LiveVisionContextRuntimeDeps(
        auto_capture_enabled=VISION_AUTO_CAPTURE_ENABLED,
        analyze_timeout_sec=VISION_ANALYZE_TIMEOUT_SEC,
        service_url=VISION_SERVICE_URL,
        capture_local_screen=capture_local_screen,
        build_observation_prompt=build_vision_observation_prompt,
        get_http_session=lambda: get_http_session(),
        client_timeout_factory=aiohttp.ClientTimeout,
        delete_request_image=delete_request_vision_image,
        format_observation=format_vision_observation,
        build_vision_quality=build_vision_quality,
        clean_text=clean_text,
        monotonic=time.monotonic,
    )


async def build_live_vision_context(user_text: str, *, metrics: dict | None = None) -> str:
    return await build_live_vision_context_from_runtime(
        user_text,
        deps=build_live_vision_context_runtime_deps(),
        metrics=metrics,
    )


def build_vision_watch_prompt() -> str:
    return build_vision_watch_prompt_from_runtime()


def vision_watch_scene_looks_bad(scene: str) -> bool:
    return vision_watch_scene_looks_bad_from_runtime(scene, deps=build_vision_watch_runtime_deps())


vision_watch_composition = VisionWatchComposition(
    VisionWatchCompositionDeps(
        enabled=VISION_WATCH_ENABLED,
        interval_sec=VISION_WATCH_INTERVAL_SEC,
        thumbnail_size=VISION_WATCH_THUMBNAIL_SIZE,
        max_image_dim=VISION_WATCH_MAX_IMAGE_DIM,
        diff_threshold=VISION_WATCH_DIFF_THRESHOLD,
        capture_all_screens=VISION_CAPTURE_ALL_SCREENS,
        analyze_cooldown_sec=VISION_WATCH_ANALYZE_COOLDOWN_SEC,
        run_ocr=VISION_WATCH_RUN_OCR,
        ocr_interval_sec=VISION_WATCH_OCR_INTERVAL_SEC,
        analyze_timeout_sec=VISION_ANALYZE_TIMEOUT_SEC,
        vision_service_url=VISION_SERVICE_URL,
        capture_frame=capture_vision_watch_frame,
        scene_looks_bad=vision_watch_scene_looks_bad,
        build_prompt=build_vision_watch_prompt,
        get_http_session=lambda: get_http_session(),
        client_timeout_factory=aiohttp.ClientTimeout,
        update_analysis=update_vision_watch_analysis,
        mark_startup_component=lambda *args, **kwargs: mark_startup_component(*args, **kwargs),
        to_thread=asyncio.to_thread,
        sleep=asyncio.sleep,
        create_task=asyncio.create_task,
        now=time.time,
        log=print,
    )
)

run_vision_watch_once = vision_watch_composition.run_vision_watch_once
vision_watch_loop = vision_watch_composition.vision_watch_loop
ensure_vision_watch_started = vision_watch_composition.ensure_vision_watch_started
stop_vision_watch_task = vision_watch_composition.stop_vision_watch_task


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
        build_evelyn_runtime_dependency_context=lambda: build_evelyn_runtime_dependency_context_from_payload(
            local_tts=local_tts_playback_manager.snapshot(),
            local_mic=serialize_local_mic_runtime_state(),
            local_only_mode=LOCAL_ONLY_MODE,
            discord_enabled=DISCORD_ENABLED,
            model_name=MODEL_NAME,
            llm_server_url=LLM_SERVER_URL,
            router_model_name=ROUTER_MODEL_NAME,
            summary_model_name=SUMMARY_MODEL_NAME,
            stt_model_name=STT_MODEL_NAME,
            stt_backend=STT_BACKEND,
            omnivoice_server_url=OMNIVOICE_SERVER_URL,
            omnivoice_voice=OMNIVOICE_VOICE,
            omnivoice_speed=OMNIVOICE_SPEED,
            voice_input_mode_status_line=voice_input_mode_status_line(),
        ),
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


def build_summary_json_llm_runtime_deps() -> JsonLlmRequestRuntimeDeps:
    return JsonLlmRequestRuntimeDeps(
        model_name=SUMMARY_MODEL_NAME,
        endpoint=SUMMARY_LLM_URL,
        model_role="summary",
        error_label="요약 LLM",
        get_http_session=get_http_session,
        client_timeout_factory=aiohttp.ClientTimeout,
        monotonic=time.monotonic,
        clean_text=clean_text,
        extract_json_object=extract_json_object_from_runtime,
        record_model_call_trace=record_model_call_trace,
    )


def build_router_json_llm_runtime_deps() -> JsonLlmRequestRuntimeDeps:
    return JsonLlmRequestRuntimeDeps(
        model_name=ROUTER_MODEL_NAME,
        endpoint=ROUTER_LLM_URL,
        model_role="router",
        error_label="router LLM",
        get_http_session=get_http_session,
        client_timeout_factory=aiohttp.ClientTimeout,
        monotonic=time.monotonic,
        clean_text=clean_text,
        extract_json_object=extract_json_object_from_runtime,
        record_model_call_trace=record_model_call_trace,
    )


def build_llm_route_runtime_deps() -> LlmRouteRuntimeDeps:
    return LlmRouteRuntimeDeps(
        classify_llm_route_fallback=classify_llm_route_fallback,
        fast_path_policy=fast_path_policy,
        session_state_snapshot=session_state_snapshot,
        load_working_summary=lambda guild_id: compact_working_summary(
            read_text_file(memory_summary_path(guild_id))
        ),
        load_cognitive_state=lambda guild_id: normalize_cognitive_state(
            read_json_file(cognitive_state_path(guild_id))
        ),
        normalize_cognitive_state=normalize_cognitive_state,
        load_recent_raw=lambda guild_id: read_jsonl(memory_raw_path(guild_id)),
        load_recent_facts=read_fact_rows,
        format_memory_rows_for_llm=format_memory_rows_for_llm,
        compact_memory_text=compact_memory_text,
        ask_router_llm=ask_router_llm,
        current_turn_id=current_turn_id,
        clean_text=clean_text,
        normalize_question_policy_mapping=normalize_question_policy_mapping,
        router_route_timeout_sec=ROUTER_ROUTE_TIMEOUT_SEC,
        cognitive_timeout_sec=COGNITIVE_TIMEOUT_SEC,
        router_llm_enabled=ROUTER_LLM_ENABLED,
        router_route_max_tokens=ROUTER_ROUTE_MAX_TOKENS,
        log=print,
    )


def build_cognitive_state_runtime_deps() -> CognitiveStateRuntimeDeps:
    return CognitiveStateRuntimeDeps(
        attach_current_task=_attach_current_task,
        detach_task=_detach_task,
        cognitive_locks=cognitive_locks,
        collect_memory_layers=collect_memory_layers,
        layered_summary_text=layered_summary_text,
        normalize_cognitive_state=normalize_cognitive_state,
        read_layered_cognitive_state=read_layered_cognitive_state,
        get_matching_speculative_policy=get_matching_speculative_policy,
        fast_path_policy=fast_path_policy,
        session_state_snapshot=session_state_snapshot,
        build_fast_cognitive_state=build_fast_cognitive_state,
        write_json_file=write_json_file,
        cognitive_state_path=cognitive_state_path,
        recent_memory_groups=recent_memory_groups,
        memory_cognitive_raw_limit=MEMORY_COGNITIVE_RAW_LIMIT,
        build_cognitive_state_messages=build_cognitive_state_messages,
        ask_router_llm=ask_router_llm,
        cognitive_max_tokens=COGNITIVE_MAX_TOKENS,
        cognitive_timeout_sec=COGNITIVE_TIMEOUT_SEC,
        current_turn_id=current_turn_id,
        is_context_size_error=is_context_size_error,
        build_compact_cognitive_state_messages=build_compact_cognitive_state_messages,
        should_log_voice_timing=should_log_voice_timing,
        build_cognitive_fallback_state=build_cognitive_fallback_state,
        finalize_cognitive_state=finalize_cognitive_state,
        log=print,
    )


cognitive_refresh_composition = CognitiveRefreshComposition(
    CognitiveRefreshCompositionDeps(
        state=build_cognitive_state_runtime_deps,
        background_tasks=background_cognitive_tasks,
        runtime_session_key=runtime_session_key,
        create_scoped_task=create_turn_scoped_task,
        current_turn_id=current_turn_id,
        monotonic=time.monotonic,
        current_task=asyncio.current_task,
        log_turn_event=log_turn_event,
        log=print,
    )
)

update_cognitive_state = cognitive_refresh_composition.update_cognitive_state
refresh_cognitive_state_in_background = (
    cognitive_refresh_composition.refresh_cognitive_state_in_background
)
schedule_cognitive_refresh = cognitive_refresh_composition.schedule_cognitive_refresh


def redact_vision_text_for_memory(text: str) -> str:
    return redact_vision_text_for_memory_payload(
        text,
        vision_memory_write_enabled=VISION_MEMORY_WRITE_ENABLED,
    )


def build_memory_update_runtime_deps() -> MemoryUpdateRuntimeDeps:
    return MemoryUpdateRuntimeDeps(
        write_memory_turn_records=write_memory_turn_records,
        vision_memory_write_enabled=VISION_MEMORY_WRITE_ENABLED,
        record_self_identity_turn=record_self_identity_turn,
        append_raw_transcript_rows=append_raw_transcript_rows,
        append_turn_rows_to_memory_vault=append_turn_rows_to_memory_vault,
        schedule_memory_vault_maintenance=schedule_memory_vault_maintenance,
        memory_refresh_inputs_for_turn=memory_refresh_inputs_for_turn,
        get_conversation_history=get_conversation_history,
        session_last_active_at=session_last_active_at,
        needs_search_or_deep_routing=needs_search_or_deep_routing,
        build_memory_writer_decision_for_turn=build_memory_writer_decision_for_turn,
        build_memory_writer_decision=build_memory_writer_decision,
        build_memory_writer_decision_payload=build_memory_writer_decision_payload,
        plan_memory_writebehind_schedule=plan_memory_writebehind_schedule,
        runtime_session_key=runtime_session_key,
        memory_writebehind_task_key=memory_writebehind_task_key,
        should_replace_existing_memory_task=should_replace_existing_memory_task,
        mark_memory_writer_status=mark_memory_writer_status,
        memory_writebehind_status_log=MEMORY_WRITEBEHIND_STATUS_LOG,
        background_memory_tasks=background_memory_tasks,
        create_turn_scoped_task=create_turn_scoped_task,
        run_memory_writebehind_steps=run_memory_writebehind_steps,
        update_long_term_memory=update_long_term_memory,
        update_cognitive_state=update_cognitive_state,
        log=print,
    )


memory_maintenance_composition = MemoryMaintenanceComposition(
    MemoryMaintenanceCompositionDeps(
        memory_update=build_memory_update_runtime_deps,
        memory_locks=memory_locks,
        background_vault_tasks=background_memory_vault_tasks,
        vault_last_maintenance_at=memory_vault_last_maintenance_at,
        attach_current_task=_attach_current_task,
        detach_task=_detach_task,
        run_long_term_memory_update=run_long_term_memory_update,
        collect_memory_layers=collect_memory_layers,
        ask_summary_llm=lambda *args, **kwargs: ask_summary_llm(*args, **kwargs),
        is_context_size_error=is_context_size_error,
        should_log_voice_timing=lambda *args, **kwargs: should_log_voice_timing(*args, **kwargs),
        memory_fact_limit=MEMORY_FACT_LIMIT,
        memory_loop_limit=MEMORY_LOOP_LIMIT,
        raw_limit=MEMORY_LONGTERM_RAW_LIMIT,
        run_vault_maintenance_once=run_memory_vault_maintenance_once,
        create_scoped_task=create_turn_scoped_task,
        lock_factory=asyncio.Lock,
        sleep=asyncio.sleep,
        to_thread=asyncio.to_thread,
        current_task=asyncio.current_task,
        monotonic=time.monotonic,
        getenv=os.getenv,
        log=print,
    )
)

update_long_term_memory = memory_maintenance_composition.update_long_term_memory
schedule_memory_vault_maintenance = (
    memory_maintenance_composition.schedule_memory_vault_maintenance
)
schedule_memory_update = memory_maintenance_composition.schedule_memory_update


async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    http_session = ensure_http_session_from_runtime(
        http_session,
        client_timeout_factory=aiohttp.ClientTimeout,
        client_session_factory=aiohttp.ClientSession,
    )
    return http_session


def build_search_answer_runtime_deps() -> SearchAnswerRuntimeDeps:
    return SearchAnswerRuntimeDeps(
        model_name=MODEL_NAME,
        llm_server_url=LLM_SERVER_URL,
        chat_content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        stop_tokens=MAIN_LLM_STOP_TOKENS,
        get_http_session=get_http_session,
        build_chat_messages=build_chat_messages,
        client_timeout_factory=aiohttp.ClientTimeout,
        clean_text=clean_text,
        sanitize_model_output=sanitize_model_output,
        strip_search_answer_sources=strip_search_answer_sources,
    )


def record_search_followup_queued() -> None:
    global search_followup_queued_count
    search_followup_queued_count += 1


def build_search_followup_runtime_deps() -> SearchFollowupRuntimeDeps:
    return SearchFollowupRuntimeDeps(
        bot=bot,
        discord_object_factory=discord.Object,
        session_followup_targets=session_followup_targets,
        background_search_tasks=background_search_tasks,
        inflight_search_tasks=inflight_search_tasks,
        apply_runtime_mode=apply_runtime_mode,
        parse_response_action_tag=parse_response_action_tag,
        answer_promises_search=answer_promises_search,
        build_search_query=build_search_query,
        runtime_session_key=runtime_session_key,
        remember_session_followup_target=remember_session_followup_target,
        get_conversation_history=get_conversation_history,
        memory_summary_path=memory_summary_path,
        read_text_file=read_text_file,
        compact_working_summary=compact_working_summary,
        search_duckduckgo=search_duckduckgo,
        answer_from_search_results=answer_from_search_results,
        resolve_open_question_rows=resolve_open_question_rows,
        write_json_file=write_json_file,
        cognitive_state_path=cognitive_state_path,
        send_discord_text=send_discord_text,
        format_display_text=format_display_text,
        speak_answer=speak_answer,
        current_turn_id=current_turn_id,
        append_history=append_history,
        schedule_memory_update=schedule_memory_update,
        create_turn_scoped_task=create_turn_scoped_task,
        attach_current_task=_attach_current_task,
        detach_task=_detach_task,
        record_search_followup_queued=record_search_followup_queued,
        log=print,
    )


runtime_lifecycle_composition = RuntimeLifecycleComposition(
    RuntimeLifecycleCompositionDeps(
        startup=RuntimeStartupCompositionDeps(
            opus=lambda: OpusStartupRuntimeDeps(
                opus_is_loaded=discord_opus.is_loaded,
                load_default_opus=discord_opus._load_default,
                mark_startup_component=mark_startup_component,
                log=print,
            ),
            stt_warmup=lambda: SttWarmupRuntimeDeps(
                mark_startup_component=mark_startup_component,
                zeros=lambda size: np.zeros(size, dtype=np.float32),
                transcribe_audio16k_sync=transcribe_audio16k_sync,
                target_rate=TARGET_RATE,
                wake_max_tokens=WAKE_MAX_TOKENS,
                log=print,
            ),
            llm_warmup=lambda: LlmWarmupRuntimeDeps(
                get_http_session=get_http_session,
                client_timeout=aiohttp.ClientTimeout,
                mark_startup_component=mark_startup_component,
                llm_server_url=LLM_SERVER_URL,
                model_name=MODEL_NAME,
                main_llm_chat_content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
                voice_llm_max_tokens=VOICE_LLM_MAX_TOKENS,
                main_llm_stop_tokens=MAIN_LLM_STOP_TOKENS,
                build_chat_messages=build_chat_messages,
                decode_sse_stream_line=decode_sse_stream_line,
                log=print,
            ),
            bot_user=lambda: bot.user,
            change_presence=bot.change_presence,
            game_factory=discord.Game,
            to_thread=asyncio.to_thread,
            create_task=asyncio.create_task,
            stt_service_url=STT_SERVICE_URL,
            get_stt_model=lambda: get_stt_model(),
            warmup_stt_sync=lambda: warmup_stt_sync(),
            warmup_llm=lambda: warmup_llm(),
            warmup_tts_server=lambda: warmup_tts_server(),
            monotonic=time.monotonic,
            log=print,
        ),
        process=RuntimeProcessCompositionDeps(
            project_root=PROJECT_ROOT,
            local_only_mode=LOCAL_ONLY_MODE,
            discord_enabled=DISCORD_ENABLED,
            control_page_port=CONTROL_PAGE_PORT,
            fallback_target=PROJECT_ROOT / "evelyn_core" / "start.bat",
            sleep=asyncio.sleep,
            stop_control_page_background_tasks=lambda: stop_control_page_background_tasks(),
            stop_vision_watch_task=lambda: stop_vision_watch_task(),
            stop_local_mic_service=lambda: stop_local_mic_service(),
            launch_runtime_restart_sequence=launch_runtime_restart_sequence,
            exit_process=os._exit,
            schedule_stack_shutdown=runtime_schedule_evelyn_stack_shutdown,
            schedule_local_shutdown=runtime_schedule_evelyn_local_shutdown,
            bot_guilds=lambda: list(bot.guilds),
            mark_startup_component=lambda *args, **kwargs: mark_startup_component(*args, **kwargs),
            start_control_page_server=lambda: start_control_page_server(),
            ensure_local_mic_service_started=lambda: ensure_local_mic_service_started(),
            ensure_vision_watch_started=lambda: ensure_vision_watch_started(),
            ensure_control_page_background_tasks_started=(
                lambda: ensure_control_page_background_tasks_started()
            ),
            control_page_local_url=lambda: control_page_local_url(),
            wait_forever=lambda: asyncio.Event().wait(),
            log=print,
        ),
    )
)

startup_components_ready = runtime_lifecycle_composition.startup_components_ready
set_tts_presence = runtime_lifecycle_composition.set_tts_presence
build_opus_startup_runtime_deps = runtime_lifecycle_composition.build_opus_startup_runtime_deps
ensure_opus_loaded = runtime_lifecycle_composition.ensure_opus_loaded
build_stt_warmup_runtime_deps = runtime_lifecycle_composition.build_stt_warmup_runtime_deps
build_llm_warmup_runtime_deps = runtime_lifecycle_composition.build_llm_warmup_runtime_deps
warmup_voice_path = runtime_lifecycle_composition.warmup_voice_path
initialize_startup_components = runtime_lifecycle_composition.initialize_startup_components
ensure_startup_components_ready = runtime_lifecycle_composition.ensure_startup_components_ready
restart_bot_process = runtime_lifecycle_composition.restart_bot_process
schedule_evelyn_stack_shutdown = runtime_lifecycle_composition.schedule_evelyn_stack_shutdown
schedule_evelyn_local_shutdown = runtime_lifecycle_composition.schedule_evelyn_local_shutdown
shutdown_bot_process = runtime_lifecycle_composition.shutdown_bot_process
run_local_only_mode = runtime_lifecycle_composition.run_local_only_mode


def resolve_evelyn_page_url() -> str | None:
    return resolve_evelyn_page_url_from_runtime(
        deps=build_evelyn_page_url_runtime_deps(
            project_root=PROJECT_ROOT,
            configured_page_url=EVELYN_PAGE_URL,
            run_git_config=subprocess.run,
        )
    )


voice_runtime_composition = VoiceRuntimeComposition(
    VoiceRuntimeCompositionDeps(
        pipeline=VoicePipelineCompositionDeps(
            project_root=PROJECT_ROOT,
            last_channel_state_file=VOICE_LAST_CHANNEL_STATE_FILE,
            summarize_p95_metrics=summarize_p95_metrics,
            merge_log_event_payload=merge_log_event_payload,
            log_turn_event=log_turn_event,
            local_only_mode=LOCAL_ONLY_MODE,
            local_tts_enabled=lambda: local_tts_playback_manager.enabled,
            local_tts_snapshot=local_tts_playback_manager.snapshot,
            voice_ingress_queue_depth=voice_ingress_queue.qsize,
            voice_ingress_queue_max=VOICE_INGRESS_QUEUE_MAX,
            live_recent_sec=VOICE_LIVE_RECENT_SEC,
            utterance_assembly_enabled=lambda: voice_utterance_assembly_config.enabled,
            utterance_pending_count=lambda: len(voice_utterance_buffers),
            utterance_commit_wait_sec=lambda: voice_utterance_assembly_config.commit_wait_sec,
            barge_in_continuity=lambda: _build_voice_barge_in_continuity_snapshot(),
            summarize_turn_path_metrics=summarize_turn_path_metrics,
            stt_cooldown_after_timeout_sec=STT_COOLDOWN_AFTER_TIMEOUT_SEC,
            monotonic=time.monotonic,
            time=time.time,
            log=print,
        ),
        debug=VoiceDebugCompositionDeps(
            project_root=PROJECT_ROOT,
            configured_dir=VOICE_DEBUG_AUDIO_DIR,
            max_files_per_guild=VOICE_DEBUG_MAX_FILES_PER_GUILD,
            max_age_days=VOICE_DEBUG_MAX_AGE_DAYS,
            max_total_bytes_per_guild=VOICE_DEBUG_MAX_TOTAL_MB_PER_GUILD * 1024 * 1024,
            preserve_newest=VOICE_DEBUG_PRESERVE_NEWEST,
            raw_channels=CHANNELS,
            raw_rate=RATE,
            stt_rate=TARGET_RATE,
            enabled=VOICE_DEBUG_SAVE_AUDIO,
            queue_max=DEBUG_WRITE_QUEUE_MAX,
            create_task=asyncio.create_task,
            to_thread=asyncio.to_thread,
            log=print,
        ),
        local_mic=LocalMicCompositionDeps(
            enabled=LOCAL_MIC_ENABLED,
            input_mode=VOICE_INPUT_MODE,
            discord_user_ids=lambda: set(LOCAL_MIC_DISCORD_USER_IDS),
            local_control_guild_id=LOCAL_CONTROL_GUILD_ID,
            local_control_guild_name=LOCAL_CONTROL_GUILD_NAME,
            local_mic_user_name=os.getenv("LOCAL_MIC_USER_NAME", "정훈"),
            normalize_voice_input_mode=normalize_voice_input_mode,
            resolve_local_mic_target=resolve_local_mic_target,
            should_route_discord_user_to_local_mic=should_route_discord_user_to_local_mic,
            guilds=lambda: list(bot.guilds),
            process_member_audio=lambda: process_member_audio,
            local_only_mode=LOCAL_ONLY_MODE,
            service_factory=LocalMicCaptureService,
            get_running_loop=asyncio.get_running_loop,
            create_task=asyncio.create_task,
            local_tts_playback_snapshot=local_tts_playback_manager.snapshot,
            tts_active_max_silence_ms=LOCAL_MIC_TTS_ACTIVE_MAX_SILENCE_MS,
            max_silence_ms=LOCAL_MIC_MAX_SILENCE_MS,
            discord_suppress_after_segment_sec=LOCAL_MIC_DISCORD_SUPPRESS_AFTER_SEGMENT_SEC,
            sample_rate=LOCAL_MIC_SAMPLE_RATE,
            block_ms=LOCAL_MIC_BLOCK_MS,
            start_threshold=LOCAL_MIC_START_THRESHOLD,
            continue_threshold=LOCAL_MIC_CONTINUE_THRESHOLD,
            start_consecutive=LOCAL_MIC_START_CONSECUTIVE,
            min_voiced_ms=LOCAL_MIC_MIN_VOICED_MS,
            preroll_ms=LOCAL_MIC_PREROLL_MS,
            max_segment_sec=LOCAL_MIC_MAX_SEGMENT_SEC,
            device=LOCAL_MIC_DEVICE,
            queue_max=LOCAL_MIC_QUEUE_MAX,
            vad_filter_enabled=LOCAL_MIC_VAD_FILTER_ENABLED,
            env_noise_filter_enabled=LOCAL_MIC_ENV_NOISE_FILTER_ENABLED,
            waveform_filter_enabled=LOCAL_MIC_WAVEFORM_FILTER_ENABLED,
            time=time.time,
            log=print,
        ),
    )
)

voice_pipeline_counters = voice_runtime_composition.voice_pipeline_counters
voice_pipeline_state = voice_runtime_composition.voice_pipeline_state
local_mic_runtime_state = voice_runtime_composition.local_mic_runtime_state
increment_voice_pipeline_counter = voice_runtime_composition.increment_voice_pipeline_counter
get_stt_inference_lock = voice_runtime_composition.get_stt_inference_lock
voice_last_channel_state_path = voice_runtime_composition.voice_last_channel_state_path
load_last_voice_channel_state = voice_runtime_composition.load_last_voice_channel_state
save_last_voice_channel_state = voice_runtime_composition.save_last_voice_channel_state
mark_voice_manual_disconnect = voice_runtime_composition.mark_voice_manual_disconnect
record_voice_pipeline_failure = voice_runtime_composition.record_voice_pipeline_failure
build_voice_pipeline_snapshot = voice_runtime_composition.build_voice_pipeline_snapshot
run_blocking_stt_task = voice_runtime_composition.run_blocking_stt_task
_save_voice_debug_audio_now = voice_runtime_composition._save_voice_debug_audio_now
debug_write_worker = voice_runtime_composition.debug_write_worker
ensure_debug_write_worker_started = voice_runtime_composition.ensure_debug_write_worker_started
save_voice_debug_audio = voice_runtime_composition.save_voice_debug_audio
local_control_voice_member = voice_runtime_composition.local_control_voice_member
is_local_speaker_voice_client = voice_runtime_composition.is_local_speaker_voice_client
stop_local_mic_service = voice_runtime_composition.stop_local_mic_service
build_local_mic_discord_suppression_runtime_deps = (
    voice_runtime_composition.build_local_mic_discord_suppression_runtime_deps
)
should_drop_discord_audio_for_local_mic = (
    voice_runtime_composition.should_drop_discord_audio_for_local_mic
)
set_voice_input_mode = voice_runtime_composition.set_voice_input_mode
voice_input_mode_status_line = voice_runtime_composition.voice_input_mode_status_line
serialize_local_mic_runtime_state = voice_runtime_composition.serialize_local_mic_runtime_state
local_mic_status_line = voice_runtime_composition.local_mic_status_line
build_local_mic_segment_runtime_deps = voice_runtime_composition.build_local_mic_segment_runtime_deps
handle_local_mic_segment = voice_runtime_composition.handle_local_mic_segment
build_local_mic_service_runtime_deps = voice_runtime_composition.build_local_mic_service_runtime_deps
ensure_local_mic_service_started = voice_runtime_composition.ensure_local_mic_service_started

atexit.register(stop_local_mic_service)


def build_tts_warmup_runtime_deps() -> TtsWarmupRuntimeDeps:
    return TtsWarmupRuntimeDeps(
        get_http_session=get_http_session,
        client_timeout=aiohttp.ClientTimeout,
        mark_startup_component=mark_startup_component,
        startup_component_done=startup_component_done,
        omnivoice_server_url=OMNIVOICE_SERVER_URL,
        omnivoice_model=OMNIVOICE_MODEL,
        omnivoice_voice=OMNIVOICE_VOICE,
        omnivoice_language=OMNIVOICE_LANGUAGE,
        getenv=os.getenv,
        log=print,
    )


def _set_tts_warmup_started(value: bool) -> None:
    global tts_warmup_started
    tts_warmup_started = value


def build_voice_timing_runtime_deps() -> VoiceTimingRuntimeDeps:
    return build_voice_timing_runtime_deps_from_runtime(
        monotonic=time.monotonic,
        voice_timing_log_threshold_ms=VOICE_TIMING_LOG_THRESHOLD_MS,
        voice_bottleneck_logs=VOICE_BOTTLENECK_LOGS,
        record_turn_stage=record_turn_stage,
        record_turn_path_summary=record_turn_path_summary,
        summarize_p95_metrics=summarize_p95_metrics,
        build_turn_summary_payload=build_turn_summary_payload,
        log_turn_event=log_turn_event,
        log=print,
    )


def build_omnivoice_request_runtime_deps() -> OmniVoiceRequestRuntimeDeps:
    return OmniVoiceRequestRuntimeDeps(
        request_id_suffix=lambda: uuid.uuid4().hex[:10],
        tts_synth_request_factory=TtsSynthRequest,
        tts_synth_result_factory=TtsSynthResult,
        omnivoice_model=OMNIVOICE_MODEL,
        omnivoice_pcm_rate=OMNIVOICE_PCM_RATE,
        omnivoice_stream=OMNIVOICE_STREAM,
        omnivoice_num_step=OMNIVOICE_NUM_STEP,
        omnivoice_speed=OMNIVOICE_SPEED,
        omnivoice_language=OMNIVOICE_LANGUAGE,
    )


def build_omnivoice_source_runtime_deps() -> OmniVoiceSourceRuntimeDeps:
    return OmniVoiceSourceRuntimeDeps(
        clean_tts_text=clean_tts_text,
        merge_log_event_payload=merge_log_event_payload,
        source_factory=OmniVoicePCMStream,
        get_http_session=get_http_session,
        client_timeout_factory=aiohttp.ClientTimeout,
        omnivoice_timeout_sec=OMNIVOICE_TIMEOUT_SEC,
        omnivoice_server_url=OMNIVOICE_SERVER_URL,
        omnivoice_voice=OMNIVOICE_VOICE,
        request_runtime_deps_factory=build_omnivoice_request_runtime_deps,
        monotonic=time.monotonic,
        log_turn_event=log_turn_event,
        record_voice_pipeline_failure=record_voice_pipeline_failure,
        create_turn_scoped_task=create_turn_scoped_task,
        log=print,
    )


# =========================================================
# STT
# =========================================================
def get_stt_model() -> tuple[str, Any, Any]:
    return get_stt_model_from_runtime(
        deps=build_stt_model_runtime_deps_from_runtime(
            stt_compute_type=STT_COMPUTE_TYPE,
            stt_model_name=STT_MODEL_NAME,
            stt_language=STT_LANGUAGE,
            stt_force_language=STT_FORCE_LANGUAGE,
            stt_max_new_tokens=max(VOICE_STT_MAX_NEW_TOKENS, 256),
            get_env_token=lambda: os.getenv("HF_TOKEN"),
            torch_device=lambda: "cuda:0" if torch.cuda.is_available() else "cpu",
            log=print,
        )
    )


def _build_stt_text_runtime_deps() -> Any:
    return build_stt_text_runtime_deps(
        clean_text=clean_text,
        normalize_voice_text=normalize_voice_text,
        contains_wake_word=contains_wake_word,
        looks_like_brief_filler_text=looks_like_brief_filler_text,
        looks_like_repetitive_noise_text=looks_like_repetitive_noise_text,
        is_similar=is_similar,
        session_partial_stt_text=session_partial_stt_text,
        session_committed_stt_text=session_committed_stt_text,
        partial_stt_cache=partial_stt_cache,
    )


def build_stt_transcription_runtime_deps() -> SttTranscriptionRuntimeDeps:
    return SttTranscriptionRuntimeDeps(
        stt_service_url=STT_SERVICE_URL,
        stt_service_timeout_sec=STT_SERVICE_TIMEOUT_SEC,
        stt_service_fallback_local=STT_SERVICE_FALLBACK_LOCAL,
        stt_language=STT_LANGUAGE,
        stt_force_language=STT_FORCE_LANGUAGE,
        target_rate=TARGET_RATE,
        normalize_stt_language=normalize_stt_language_from_runtime,
        transcribe_via_service=transcribe_audio16k_via_service,
        get_stt_model=get_stt_model,
        as_float32_array=lambda audio: np.asarray(audio, dtype=np.float32),
        resample_audio_float=resample_audio_float,
        clean_text=clean_text,
        log=print,
    )


# =========================================================
# 디스코드 음성
# =========================================================
def build_discord_voice_connection_runtime_deps() -> DiscordVoiceConnectionRuntimeDeps:
    return DiscordVoiceConnectionRuntimeDeps(
        voice_client_type=EvelynVoiceClient,
        voice_connect_locks=voice_connect_locks,
        voice_connect_timeout=VOICE_CONNECT_TIMEOUT,
        voice_connect_retries=VOICE_CONNECT_RETRIES,
        voice_connect_retry_delay_sec=VOICE_CONNECT_RETRY_DELAY_SEC,
        process_member_audio=process_member_audio,
        sleep=asyncio.sleep,
        log=print,
    )


voice_support_composition = VoiceSupportComposition(
    VoiceSupportCompositionDeps(
        continuity=lambda: build_voice_barge_in_continuity_runtime_deps(),
        stt_warmup=lambda: build_stt_warmup_runtime_deps(),
        tts_warmup=lambda: build_tts_warmup_runtime_deps(),
        timing=lambda: build_voice_timing_runtime_deps(),
        omnivoice_source=lambda: build_omnivoice_source_runtime_deps(),
        stt_transcription=lambda: build_stt_transcription_runtime_deps(),
        stt_text=lambda: _build_stt_text_runtime_deps(),
        voice_connection=lambda: build_discord_voice_connection_runtime_deps(),
        set_tts_warmup_started=_set_tts_warmup_started,
        partial_stt_max_new_tokens=max(64, min(VOICE_STT_MAX_NEW_TOKENS, 128)),
        clean_text=clean_text,
        wake_audio_sec=WAKE_AUDIO_SEC,
        wake_confirm_audio_sec=WAKE_CONFIRM_AUDIO_SEC,
        wake_max_tokens=WAKE_MAX_TOKENS,
        wake_confirm_max_tokens=WAKE_CONFIRM_MAX_TOKENS,
        apply_stt_post_corrections=apply_stt_post_corrections,
        strip_leading_voice_fillers=strip_leading_voice_fillers,
        extract_leading_wake_alias=extract_leading_wake_alias,
        fuzzy_leading_wake_alias=fuzzy_leading_wake_alias,
        looks_like_gibberish_probe=looks_like_gibberish_probe,
        slice_audio_window=slice_audio_window,
        ensure_startup_components_ready=ensure_startup_components_ready,
        voice_client_type=EvelynVoiceClient,
        process_member_audio=lambda: process_member_audio,
        warmup_voice_path=warmup_voice_path,
        save_last_voice_channel_state=save_last_voice_channel_state,
        load_last_voice_channel_state=load_last_voice_channel_state,
        increment_voice_pipeline_counter=increment_voice_pipeline_counter,
        voice_pipeline_state=voice_pipeline_state,
        voice_rejoin_on_ready=VOICE_REJOIN_ON_READY,
        get_guild=bot.get_guild,
        voice_channel_type=discord.VoiceChannel,
        now=time.time,
        log=print,
    )
)

_parse_barge_in_reason_label = voice_support_composition.parse_barge_in_reason_label
_format_voice_barge_in_continuity_summary = voice_support_composition.format_voice_barge_in_continuity_summary
_format_voice_barge_in_continuity_detail_lines = voice_support_composition.format_voice_barge_in_continuity_detail_lines
start_voice_barge_in_continuity_probe = voice_support_composition.start_voice_barge_in_continuity_probe
_build_voice_barge_in_continuity_snapshot = voice_support_composition.build_voice_barge_in_continuity_snapshot
reset_voice_barge_in_continuity_probe = voice_support_composition.reset_voice_barge_in_continuity_probe
_mark_voice_barge_in_continuity_probe = voice_support_composition.mark_voice_barge_in_continuity_probe
warmup_stt_sync = voice_support_composition.warmup_stt_sync
warmup_tts_server = voice_support_composition.warmup_tts_server
should_log_voice_timing = voice_support_composition.should_log_voice_timing
log_voice_latency = voice_support_composition.log_voice_latency
log_voice_stage = voice_support_composition.log_voice_stage
log_voice_bottleneck_summary = voice_support_composition.log_voice_bottleneck_summary
create_omnivoice_source = voice_support_composition.create_omnivoice_source
transcribe_audio16k_sync = voice_support_composition.transcribe_audio16k_sync
build_partial_stt_window = voice_support_composition.build_partial_stt_window
longest_common_prefix_text = voice_support_composition.longest_common_prefix_text
commit_stable_transcript = voice_support_composition.commit_stable_transcript
get_partial_transcript = voice_support_composition.get_partial_transcript
score_stt_candidate = voice_support_composition.score_stt_candidate
choose_full_stt_candidate = voice_support_composition.choose_full_stt_candidate
detect_wake_word_sync = voice_support_composition.detect_wake_word_sync
_wait_for_internal_voice_reconnect = voice_support_composition.wait_for_internal_voice_reconnect
connect_evelyn_voice_client = voice_support_composition.connect_evelyn_voice_client
ensure_listening_voice_client = voice_support_composition.ensure_listening_voice_client
ensure_voice_client = voice_support_composition.ensure_voice_client
restore_last_voice_channel = voice_support_composition.restore_last_voice_channel


def build_tts_interrupt_runtime_deps() -> TtsInterruptRuntimeDeps:
    return TtsInterruptRuntimeDeps(
        tts_playback_manager=tts_playback_manager,
        log_turn_event=log_turn_event,
        speaker_verification_applies=speaker_verification_applies,
        speaker_verification_result_factory=SpeakerVerificationResult,
        speaker_verifier=speaker_verifier,
        speaker_verification_apply_to=SPEAKER_VERIFICATION_APPLY_TO,
        speaker_verification_threshold=SPEAKER_VERIFICATION_THRESHOLD,
        to_thread=asyncio.to_thread,
    )


def build_cached_tts_runtime_deps() -> CachedTtsRuntimeDeps:
    return CachedTtsRuntimeDeps(
        resolve_cached_tts_audio_path=resolve_cached_tts_audio_path,
        cached_audio_enabled=CACHED_AUDIO_ENABLED,
        canned_wake_reply_text=CANNED_WAKE_REPLY_TEXT,
        canned_wake_reply_audio=CANNED_WAKE_REPLY_AUDIO,
        project_root=PROJECT_ROOT,
        cached_wave_audio_source_factory=CachedWaveAudioSource,
        tts_source_playback_request_factory=TtsSourcePlaybackRequest,
        tts_playback_manager=tts_playback_manager,
        clean_text=clean_text,
        log_turn_event=log_turn_event,
        log_voice_latency=log_voice_latency,
    )


def build_discord_tts_single_runtime_deps() -> DiscordTtsSingleRuntimeDeps:
    return DiscordTtsSingleRuntimeDeps(
        is_local_speaker_voice_client=is_local_speaker_voice_client,
        speak_answer_local=speak_answer_local,
        tts_running_state=TurnState.TTS_RUNNING,
        play_cached_answer_audio=play_cached_answer_audio,
        tts_lock=tts_lock,
        create_omnivoice_source=create_omnivoice_source,
        log_turn_event=log_turn_event,
        log_voice_latency=log_voice_latency,
        playback_manager=tts_playback_manager,
        source_playback_request_factory=TtsSourcePlaybackRequest,
    )


def build_discord_tts_stream_runtime_deps() -> DiscordTtsStreamRuntimeDeps:
    return DiscordTtsStreamRuntimeDeps(
        attach_current_task=_attach_current_task,
        detach_task=_detach_task,
        tts_running_state=TurnState.TTS_RUNNING,
        create_omnivoice_source=create_omnivoice_source,
        mark_turn_stage=mark_turn_stage,
        log_voice_latency=log_voice_latency,
        log_turn_event=log_turn_event,
        record_voice_pipeline_failure=record_voice_pipeline_failure,
        tts_lock=tts_lock,
        playback_manager=tts_playback_manager,
        streaming_playback_request_factory=TtsStreamingPlaybackRequest,
        omnivoice_timeout_sec=OMNIVOICE_TIMEOUT_SEC,
        tts_prefetch_chunks=TTS_PREFETCH_CHUNKS,
        playback_start_lookahead_chunks=TTS_PLAYBACK_START_LOOKAHEAD_CHUNKS,
        playback_start_lookahead_timeout_ms=TTS_PLAYBACK_START_LOOKAHEAD_TIMEOUT_MS,
        create_turn_scoped_task=create_turn_scoped_task,
        log=print,
    )


def build_local_tts_single_runtime_deps() -> LocalTtsSingleRuntimeDeps:
    return LocalTtsSingleRuntimeDeps(
        playback_manager=local_tts_playback_manager,
        clean_tts_text=clean_tts_text,
        strip_omnivoice_tags=strip_omnivoice_tags,
        attach_current_task=_attach_current_task,
        detach_task=_detach_task,
        tts_running_state=TurnState.TTS_RUNNING,
        tts_lock=tts_lock,
        create_omnivoice_source=create_omnivoice_source,
        mark_turn_stage=mark_turn_stage,
        log_voice_latency=log_voice_latency,
        log_turn_event=log_turn_event,
        mark_local_tts_first_playback=_mark_local_tts_first_playback,
        record_voice_pipeline_failure=record_voice_pipeline_failure,
        omnivoice_timeout_sec=OMNIVOICE_TIMEOUT_SEC,
    )


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


def build_local_tts_stream_runtime_deps() -> LocalTtsStreamRuntimeDeps:
    return LocalTtsStreamRuntimeDeps(
        playback_manager=local_tts_playback_manager,
        attach_current_task=_attach_current_task,
        detach_task=_detach_task,
        tts_running_state=TurnState.TTS_RUNNING,
        clean_tts_text=clean_tts_text,
        strip_omnivoice_tags=strip_omnivoice_tags,
        create_omnivoice_source=create_omnivoice_source,
        mark_turn_stage=mark_turn_stage,
        log_voice_latency=log_voice_latency,
        log_turn_event=log_turn_event,
        record_voice_pipeline_failure=record_voice_pipeline_failure,
        tts_lock=tts_lock,
        tts_prefetch_chunks=TTS_PREFETCH_CHUNKS,
        create_turn_scoped_task=create_turn_scoped_task,
        prefetch_tts_sources=prefetch_tts_sources,
        omnivoice_timeout_sec=OMNIVOICE_TIMEOUT_SEC,
        cleanup_prepared_tts_item=_cleanup_prepared_tts_item,
        mark_local_tts_first_playback=_mark_local_tts_first_playback,
    )


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
    return schedule_local_control_tts_from_runtime(
        answer,
        turn_id=turn_id,
        session_key=session_key,
        turn_scope=turn_scope,
        deps=build_local_control_tts_runtime_deps(
            local_only_mode=LOCAL_ONLY_MODE,
            local_tts_enabled=lambda: bool(local_tts_playback_manager.enabled),
            speak_answer_local=speak_answer_local,
            create_turn_scoped_task=create_turn_scoped_task,
            log_voice_bottleneck_summary=log_voice_bottleneck_summary,
            monotonic=time.monotonic,
        ),
    )


# =========================================================
# LLM
# =========================================================
def fallback_answer_for(user_text: str) -> str:
    user_text = clean_text(user_text)
    if not user_text:
        return "응, 듣고 있어."
    return "응, 잠깐만."


def build_voice_response_runtime_deps() -> VoiceResponseRuntimeDeps:
    return VoiceResponseRuntimeDeps(
        model_name=MODEL_NAME,
        llm_server_url=LLM_SERVER_URL,
        main_llm_chat_content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        main_llm_stop_tokens=tuple(MAIN_LLM_STOP_TOKENS),
        voice_llm_max_tokens=VOICE_LLM_MAX_TOKENS,
        get_http_session=get_http_session,
        build_chat_messages=build_chat_messages,
        fallback_answer_for=fallback_answer_for,
        split_tts_sentences=split_tts_sentences,
        build_answer_payload_from_text=build_answer_payload_from_text,
        log_voice_stage=log_voice_stage,
        prepare_route_context=prepare_route_context,
        prepare_llm_messages=prepare_llm_messages,
        is_user_echo_answer=is_user_echo_answer,
        is_casual_call_or_status_question=session_is_casual_call_or_status_question,
        observe_live_minecraft_state=observe_live_minecraft_state,
        build_runtime_status_context=build_runtime_status_context,
        build_main_response_guidance=build_main_response_guidance,
        sanitize_model_output=sanitize_model_output,
        parse_response_action_tag=parse_response_action_tag,
        extract_answer_from_reasoning=extract_answer_from_reasoning,
        sanitize_unrequested_minecraft_leak=sanitize_unrequested_minecraft_leak,
        enforce_question_limits=enforce_question_limits,
        record_question_trace=record_question_trace,
        format_minecraft_state_summary=format_minecraft_state_summary,
        log=print,
    )


def build_main_llm_runtime_deps() -> MainLlmRuntimeDeps:
    return MainLlmRuntimeDeps(
        model_name=MODEL_NAME,
        llm_server_url=LLM_SERVER_URL,
        main_llm_chat_content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        main_llm_stop_tokens=tuple(MAIN_LLM_STOP_TOKENS),
        voice_llm_max_tokens=VOICE_LLM_MAX_TOKENS,
        get_http_session=get_http_session,
        fallback_answer_for=fallback_answer_for,
        extract_main_llm_answer_from_choice=extract_main_llm_answer_from_choice,
        sanitize_model_output=sanitize_model_output,
        parse_response_action_tag=parse_response_action_tag,
        extract_answer_from_reasoning=extract_answer_from_reasoning,
        compact_memory_text=compact_memory_text,
        build_main_response_guidance=build_main_response_guidance,
        build_main_llm_payload=build_main_llm_payload,
        strip_search_answer_sources=strip_search_answer_sources,
        enforce_question_limits=enforce_question_limits,
        record_question_trace=record_question_trace,
        answer_promises_search=answer_promises_search,
        has_negated_search_marker=has_negated_search_marker,
        execute_search_then_answer_action=execute_search_then_answer_action,
        log=print,
    )


def build_ask_llm_once_runtime_deps() -> AskLlmOnceRuntimeDeps:
    return AskLlmOnceRuntimeDeps(
        log_voice_stage=log_voice_stage,
        clean_text=clean_text,
        prepare_route_context=prepare_route_context,
        maybe_execute_registered_route=maybe_execute_registered_route,
        is_user_echo_answer=is_user_echo_answer,
        update_session_state=update_session_state,
        build_answer_payload_from_text=build_answer_payload_from_text,
        session_is_casual_call_or_status_question=session_is_casual_call_or_status_question,
        observe_live_minecraft_state=observe_live_minecraft_state,
        build_runtime_status_context=build_runtime_status_context,
        build_main_response_guidance=build_main_response_guidance,
        build_main_llm_payload=build_main_llm_payload,
        execute_main_llm_once=execute_main_llm_once,
        sanitize_unrequested_minecraft_leak=sanitize_unrequested_minecraft_leak,
        resolve_promised_search_final_answer=resolve_promised_search_final_answer,
        enforce_question_limits=enforce_question_limits,
        record_question_trace=record_question_trace,
        model_name=MODEL_NAME,
        main_llm_chat_content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        voice_llm_max_tokens=VOICE_LLM_MAX_TOKENS,
        main_llm_stop_tokens=MAIN_LLM_STOP_TOKENS,
    )


def build_voice_stream_chunk_deps() -> VoiceStreamChunkDeps:
    return VoiceStreamChunkDeps(
        tts_first_chunk_min_chars=TTS_FIRST_CHUNK_MIN_CHARS,
        tts_first_chunk_target_chars=TTS_FIRST_CHUNK_TARGET_CHARS,
        tts_first_chunk_max_chars=TTS_FIRST_CHUNK_MAX_CHARS,
        tts_next_chunk_min_chars=TTS_NEXT_CHUNK_MIN_CHARS,
        tts_next_chunk_target_chars=TTS_NEXT_CHUNK_TARGET_CHARS,
        tts_next_chunk_max_chars=TTS_NEXT_CHUNK_MAX_CHARS,
    )


DEFAULT_INTERNAL_ROUTES = {"main_direct", "policy_short_circuit", "search_executor", "routing", "delivery"}
DISABLED_MAIN_APP_SKILL_ROUTES = {"minecraft"}


def build_route_executor_runtime_deps() -> ResolveRouteExecutorRuntimeDeps:
    return ResolveRouteExecutorRuntimeDeps(
        get_autonomy_engine=lambda guild_id: autonomy_engines.get(guild_id),
        create_autonomy_engine=get_or_create_autonomy_engine,
    )


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


def build_minecraft_live_observation_runtime_deps() -> MinecraftLiveObservationRuntimeDeps:
    return MinecraftLiveObservationRuntimeDeps(
        get_minecraft_client=get_minecraft_client,
        merge_voyager_status_into_state=merge_voyager_status_into_state,
        attach_minecraft_runtime_snapshot=attach_minecraft_runtime_snapshot,
        clean_text=clean_text,
        now=time.time,
        stale_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC,
        expired_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC,
    )


async def observe_live_minecraft_state(guild_id: int | None) -> dict[str, Any] | None:
    return await observe_live_minecraft_state_from_runtime(
        guild_id,
        deps=build_minecraft_live_observation_runtime_deps(),
    )


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


def build_control_page_ui_runtime_deps() -> ControlPageUiRuntimeDeps:
    return ControlPageUiRuntimeDeps(
        control_page_host=CONTROL_PAGE_HOST,
        control_page_port=CONTROL_PAGE_PORT,
        local_control_guild_id=LOCAL_CONTROL_GUILD_ID,
        local_control_guild_name=LOCAL_CONTROL_GUILD_NAME,
        control_page_welcome_fallback=CONTROL_PAGE_WELCOME_FALLBACK,
        clean_text=clean_text,
        sanitize_control_page_welcome_text_payload=sanitize_control_page_welcome_text_payload,
        control_page_ui_command_store=control_page_ui_command_store,
        control_page_chat_log_store=control_page_chat_log_store,
    )


def build_control_page_guild_selection_runtime_deps() -> ControlPageGuildSelectionRuntimeDeps:
    return ControlPageGuildSelectionRuntimeDeps(
        get_requested_guild=lambda guild_id: bot.get_guild(int(guild_id)),
        bot_guilds=lambda: bot.guilds,
        tracked_tts_playback_guild_ids=lambda: tracked_tts_playback_guild_ids(tts_playback_tracker),
        get_tracked_tts_playback=lambda guild_id: get_tracked_tts_playback(tts_playback_tracker, int(guild_id)),
        get_active_session_user_id=lambda session_key: active_session_user_ids.get(str(session_key)),
        get_guild_member=lambda guild, user_id: guild.get_member(int(user_id)),
        clean_text=clean_text,
    )

def build_control_page_welcome_runtime_deps() -> ControlPageWelcomeRuntimeDeps:
    return ControlPageWelcomeRuntimeDeps(
        effective_guild_name=control_page_composition.effective_guild_name,
        effective_guild_id=control_page_effective_guild_id,
        build_main_llm_payload=build_main_llm_payload,
        model_name=MODEL_NAME,
        main_llm_chat_content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        main_llm_stop_tokens=MAIN_LLM_STOP_TOKENS,
        get_http_session=get_http_session,
        client_timeout_factory=aiohttp.ClientTimeout,
        welcome_llm_timeout_sec=CONTROL_PAGE_WELCOME_LLM_TIMEOUT_SEC,
        llm_server_url=LLM_SERVER_URL,
        extract_main_llm_answer_from_choice=extract_main_llm_answer_from_choice,
        sanitize_model_output=sanitize_model_output,
        parse_response_action_tag=parse_response_action_tag,
        extract_answer_from_reasoning=extract_answer_from_reasoning,
        sanitize_welcome_text=control_page_composition.sanitize_welcome_text,
        record_model_call_trace=record_model_call_trace,
        monotonic=time.monotonic,
        welcome_fallback=CONTROL_PAGE_WELCOME_FALLBACK,
        clean_text=clean_text,
        log=print,
    )


def build_control_page_minecraft_live_snapshot_runtime_deps() -> ControlPageMinecraftLiveSnapshotRuntimeDeps:
    return ControlPageMinecraftLiveSnapshotRuntimeDeps(
        get_minecraft_client=get_minecraft_client,
        observe_live_minecraft_state=observe_live_minecraft_state,
        merge_voyager_status_into_state=merge_voyager_status_into_state,
        normalize_inventory_top_entries=normalize_inventory_top_entries,
        summarize_inventory_top=summarize_inventory_top,
        normalize_inventory_slot_entries=normalize_inventory_slot_entries,
        normalize_inventory_used_slots=normalize_inventory_used_slots,
        extract_recent_activity=extract_minecraft_recent_activity_live,
        format_position_short=format_position_short,
        attach_minecraft_runtime_snapshot=attach_minecraft_runtime_snapshot,
        clean_text=clean_text,
        now=time.time,
        stale_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC,
        expired_after_sec=CONTROL_PAGE_MINECRAFT_CACHE_MAX_STALE_SEC,
    )


def build_control_page_runtime_services_runtime_deps() -> ControlPageRuntimeServicesRuntimeDeps:
    return ControlPageRuntimeServicesRuntimeDeps(
        cache=control_page_runtime_services_cache,
        get_refresh_task=lambda: control_page_runtime_services_refresh_task,
        set_refresh_task=_set_control_page_runtime_services_refresh_task,
        get_lock=lambda: control_page_runtime_services_lock,
        set_lock=_set_control_page_runtime_services_lock,
        lock_factory=asyncio.Lock,
        create_task=asyncio.create_task,
        probe_runtime_services_once=lambda: probe_control_page_runtime_services_once_from_runtime(
            deps=build_control_page_runtime_services_probe_runtime_deps(),
        ),
        build_runtime_services_error_payload=build_control_page_runtime_services_error_payload,
        clean_text=clean_text,
        action_backend=VOYAGER_ACTION_BACKEND,
        now=time.time,
    )


def build_control_page_runtime_services_probe_runtime_deps() -> ControlPageRuntimeServicesProbeDeps:
    return ControlPageRuntimeServicesProbeDeps(
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
        voyager_alive_probe=lambda: get_minecraft_client().is_service_alive(timeout_sec=0.45),
        probe_runtime_services_once=probe_control_page_runtime_services,
    )


def _set_control_page_runtime_services_refresh_task(task: asyncio.Task | None) -> None:
    global control_page_runtime_services_refresh_task
    control_page_runtime_services_refresh_task = task


def _set_control_page_runtime_services_lock(lock: asyncio.Lock) -> None:
    global control_page_runtime_services_lock
    control_page_runtime_services_lock = lock


def build_control_page_minecraft_snapshot_runtime_deps() -> ControlPageMinecraftSnapshotRuntimeDeps:
    return ControlPageMinecraftSnapshotRuntimeDeps(
        cache=control_page_minecraft_snapshot_cache,
        get_refresh_task=lambda: control_page_minecraft_snapshot_refresh_task,
        set_refresh_task=_set_control_page_minecraft_snapshot_refresh_task,
        get_lock=lambda: control_page_minecraft_snapshot_lock,
        set_lock=_set_control_page_minecraft_snapshot_lock,
        lock_factory=asyncio.Lock,
        create_task=asyncio.create_task,
        wait_for=asyncio.wait_for,
        get_snapshot=get_control_page_minecraft_snapshot,
        clean_text=clean_text,
        timeout_sec=CONTROL_PAGE_MINECRAFT_SNAPSHOT_TIMEOUT_SEC,
    )


def _set_control_page_minecraft_snapshot_refresh_task(task: asyncio.Task | None) -> None:
    global control_page_minecraft_snapshot_refresh_task
    control_page_minecraft_snapshot_refresh_task = task


def _set_control_page_minecraft_snapshot_lock(lock: asyncio.Lock) -> None:
    global control_page_minecraft_snapshot_lock
    control_page_minecraft_snapshot_lock = lock


def build_control_page_background_tasks_runtime_deps() -> ControlPageBackgroundTasksRuntimeDeps:
    return ControlPageBackgroundTasksRuntimeDeps(
        get_poll_task=lambda: control_page_minecraft_snapshot_poll_task,
        set_poll_task=_set_control_page_minecraft_snapshot_poll_task,
        get_snapshot_refresh_task=lambda: control_page_minecraft_snapshot_refresh_task,
        set_snapshot_refresh_task=_set_control_page_minecraft_snapshot_refresh_task,
        get_runtime_services_refresh_task=lambda: control_page_runtime_services_refresh_task,
        set_runtime_services_refresh_task=_set_control_page_runtime_services_refresh_task,
        create_task=asyncio.create_task,
        select_control_page_guild=control_page_composition.select_guild,
        ensure_minecraft_snapshot=ensure_control_page_minecraft_snapshot,
        sleep=asyncio.sleep,
        log=print,
        refresh_interval_sec=CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC,
    )


def _set_control_page_minecraft_snapshot_poll_task(task: asyncio.Task | None) -> None:
    global control_page_minecraft_snapshot_poll_task
    control_page_minecraft_snapshot_poll_task = task


def build_control_page_status_runtime_deps() -> ControlPageStatusRuntimeDeps:
    return ControlPageStatusRuntimeDeps(
        model_name=MODEL_NAME,
        router_model_name=ROUTER_MODEL_NAME,
        summary_model_name=SUMMARY_MODEL_NAME,
        stt_model_name=STT_MODEL_NAME,
        discord_enabled=DISCORD_ENABLED,
        bot_api_host=CONTROL_PAGE_BOT_API_HOST,
        bot_api_port=CONTROL_PAGE_BOT_API_PORT,
        control_page_local_url=control_page_local_url,
        voice_input_mode_status_line=voice_input_mode_status_line,
        local_mic_status_line=local_mic_status_line,
        current_tts_target_name=current_tts_target_name,
        is_tracked_tts_playback_active=lambda guild_id: is_tracked_tts_playback_active(tts_playback_tracker, guild_id),
        local_tts_snapshot=local_tts_playback_manager.snapshot,
        local_mic_runtime_state=serialize_local_mic_runtime_state,
        build_voice_pipeline_snapshot=build_voice_pipeline_snapshot,
        format_voice_continuity_detail_lines=_format_voice_barge_in_continuity_detail_lines,
        build_status_text_payload=build_control_page_status_text_payload,
        build_local_status_text_payload=build_control_page_local_status_text_payload,
        build_voice_status_reply_payload=build_control_page_voice_status_reply_payload,
        build_voice_continuity_reply_payload=build_control_page_voice_continuity_reply_payload,
        get_control_page_minecraft_snapshot=control_page_composition.safe_get_minecraft_snapshot,
        build_control_page_inventory_reply_payload=build_control_page_inventory_reply_payload,
        build_control_page_minecraft_reply_payload=build_control_page_minecraft_reply_payload,
        get_autonomy_engine=autonomy_engines.get,
        get_routed_autonomy_executor=get_routed_autonomy_executor,
        build_control_page_autonomy_reply_payload=build_control_page_autonomy_reply_payload,
    )


def build_control_page_tool_runtime_deps() -> ControlPageToolRuntimeDeps:
    return ControlPageToolRuntimeDeps(
        clean_text=clean_text,
        enqueue_control_page_ui_command=control_page_composition.enqueue_ui_command,
        memory_panel_reply=memory_panel_reply,
        create_task=asyncio.create_task,
        restart_bot_process=restart_bot_process,
        recent_history_for_router=session_state_store.recent_history_for_router,
        record_tool_assistant_turn=session_state_store.record_tool_assistant_turn,
        control_page_effective_guild_id=control_page_effective_guild_id,
        control_page_session_key=control_page_session_key,
        system_prompt=SYSTEM_PROMPT,
        max_history_items=MAX_HISTORY,
        active_conversation_text_sec=ACTIVE_CONVERSATION_TEXT_SEC,
        router_llm_enabled=ROUTER_LLM_ENABLED,
        route_timeout_sec=ROUTER_ROUTE_TIMEOUT_SEC,
        control_page_tool_registry_prompt=control_page_tool_registry_prompt,
        ask_router_llm=ask_router_llm,
        current_turn_id=current_turn_id,
        log=print,
        control_page_tool_policy_error=control_page_tool_policy_error,
        build_control_page_help_reply=build_control_page_help_reply,
        execute_control_page_memory_tool=execute_control_page_memory_tool,
        execute_control_page_runtime_tool=execute_control_page_runtime_tool,
        execute_control_page_voice_tool=execute_control_page_voice_tool,
        execute_control_page_minecraft_tool=execute_control_page_minecraft_tool,
        ensure_vault_layout=ensure_memory_vault_layout,
        open_vault_tool_reply=control_page_open_memory_vault_tool_reply,
        vault_obsidian_url=memory_vault_obsidian_url,
        open_url=open_control_page_url_with_system,
        open_path=open_control_page_path_with_system,
        guild_getter_runtime={
            "get_runtime_services": get_control_page_runtime_services,
            "build_local_status_text": control_page_composition.build_local_status_text,
            "build_status_reply": control_page_composition.build_status_reply,
            "schedule_local_shutdown": schedule_evelyn_local_shutdown,
            "schedule_stack_shutdown": schedule_evelyn_stack_shutdown,
            "schedule_bot_shutdown": lambda: asyncio.create_task(shutdown_bot_process()),
            "build_autonomy_reply": control_page_composition.build_autonomy_reply,
            "build_voice_status_reply": control_page_composition.build_voice_status_reply,
            "set_input_mode": set_voice_input_mode,
            "input_mode_status_line": voice_input_mode_status_line,
            "restore_voice_channel": restore_last_voice_channel,
            "build_voice_continuity_reply": control_page_composition.build_voice_continuity_reply,
            "reset_continuity_probe": reset_voice_barge_in_continuity_probe,
            "build_inventory_reply": control_page_composition.build_inventory_reply,
            "build_minecraft_reply": control_page_composition.build_minecraft_reply,
            "enable_mode": enable_minecraft_mode,
            "disable_mode": disable_minecraft_mode,
            "get_client": get_minecraft_client,
            "format_position": format_position_short,
        },
    )


def build_control_page_search_runtime_deps() -> ControlPageSearchRuntimeDeps:
    return ControlPageSearchRuntimeDeps(
        control_page_effective_guild_id=control_page_effective_guild_id,
        control_page_session_key=control_page_session_key,
        get_conversation_history=get_conversation_history,
        build_route_decision=build_route_decision,
        monotonic=time.monotonic,
        execute_search_then_answer_action=execute_search_then_answer_action,
        synthesize_tool_result_with_main_llm=synthesize_tool_result_with_main_llm,
        clean_text=clean_text,
        get_session_lock=lambda session_key: session_locks.setdefault(session_key, asyncio.Lock()),
        append_history=append_history,
        mark_session_active=mark_session_active,
        active_conversation_text_sec=ACTIVE_CONVERSATION_TEXT_SEC,
        build_topic_id=build_topic_id,
        schedule_local_control_tts=schedule_local_control_tts,
        current_turn_id=current_turn_id,
        format_display_text=format_display_text,
        fallback_answer_for=fallback_answer_for,
    )


def build_control_page_text_runtime_deps() -> ControlPageTextRuntimeDeps:
    return ControlPageTextRuntimeDeps(
        effective_guild_id=control_page_effective_guild_id,
        session_key_for_guild=control_page_session_key,
        get_session_lock=lambda session_key: session_locks.setdefault(session_key, asyncio.Lock()),
        begin_user_text_turn=begin_user_text_turn,
        turn_scope_factory=TurnScope,
        replace_room_turn_scope=replace_room_turn_scope,
        attach_current_task=_attach_current_task,
        monotonic=time.monotonic,
        resolve_pending_proactive_question_for_turn=resolve_pending_proactive_question_for_turn,
        ask_llm_streaming=ask_llm_streaming,
        clean_text=clean_text,
        strip_omnivoice_tags=strip_omnivoice_tags,
        session_state_snapshot=session_state_snapshot,
        maybe_append_proactive_question=maybe_append_proactive_question,
        finish_assistant_text_turn=finish_assistant_text_turn,
        log_voice_bottleneck_summary=log_voice_bottleneck_summary,
        schedule_local_control_tts=schedule_local_control_tts,
        format_display_text=format_display_text,
        fallback_answer_for=fallback_answer_for,
        detach_task=_detach_task,
        clear_room_turn_scope=clear_room_turn_scope,
    )


def build_control_page_input_runtime_deps() -> ControlPageInputRuntimeDeps:
    return ControlPageInputRuntimeDeps(
        clean_text=clean_text,
        control_page_effective_guild_id=control_page_effective_guild_id,
        control_page_session_key=control_page_session_key,
        cheap_control_page_tool_decision=cheap_control_page_tool_decision,
        execute_control_page_tool=control_page_composition.execute_tool,
        remember_control_page_tool_turn=control_page_composition.remember_tool_turn,
        should_route_control_page_tool_candidate=should_route_control_page_tool_candidate,
        decide_control_page_tool_call=control_page_composition.decide_tool_call,
        control_page_tool_decision_from_llm=control_page_tool_decision_from_llm,
        control_page_tool_policy_error=control_page_tool_policy_error,
        control_page_tool_reply_from_execution=control_page_tool_reply_from_execution,
        should_force_search_query=should_force_search_query,
        answer_control_page_search_text=control_page_composition.answer_search_text,
        answer_control_page_text=control_page_composition.answer_text,
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
            ensure_welcome_message=control_page_composition.ensure_welcome_message,
            build_commands=build_control_page_commands,
            build_all_commands=build_control_page_all_commands,
            build_boot_progress=control_page_composition.build_boot_progress,
            local_tts_snapshot=local_tts_playback_manager.snapshot,
            serialize_local_mic_state=serialize_local_mic_runtime_state,
            read_vision_watch_state=read_vision_watch_state,
            build_panel_state=control_page_composition.build_panel_state,
            local_url=control_page_local_url,
            get_chat_log=control_page_composition.get_chat_log,
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
            build_local_status_text=control_page_composition.build_local_status_text,
            ensure_minecraft_snapshot=ensure_control_page_minecraft_snapshot,
            minecraft_snapshot_has_value=control_page_minecraft_snapshot_cache.has_snapshot,
            minecraft_snapshot_copy=control_page_composition.get_minecraft_snapshot_cache_copy,
            is_tts_active=lambda guild_id: is_tracked_tts_playback_active(tts_playback_tracker, guild_id),
            current_tts_target_name=current_tts_target_name,
            serialize_local_mic_target=serialize_local_mic_target,
            resolve_local_mic_target=resolve_local_mic_target,
            guilds=bot.guilds,
            local_mic_discord_user_ids=LOCAL_MIC_DISCORD_USER_IDS,
            voice_debug_audio=VOICE_DEBUG_SAVE_AUDIO,
            build_status_text=control_page_composition.build_status_text,
        ),
    )


open_control_page_path_with_system = open_path_with_system
open_control_page_url_with_system = open_url_with_system


def _set_control_page_runner(runner: web.AppRunner) -> None:
    global control_page_runner
    control_page_runner = runner


def _set_control_page_site(site: web.TCPSite) -> None:
    global control_page_site
    control_page_site = site


def _set_control_page_start_lock(lock: asyncio.Lock) -> None:
    global control_page_start_lock
    control_page_start_lock = lock


def build_control_page_server_start_runtime_deps() -> ControlPageServerStartRuntimeDeps:
    return control_page_http_composition.build_server_start_deps()


control_page_composition = ControlPageComposition(
    ControlPageCompositionDeps(
        ui=lambda: build_control_page_ui_runtime_deps(),
        guild_selection=lambda: build_control_page_guild_selection_runtime_deps(),
        welcome=lambda: build_control_page_welcome_runtime_deps(),
        minecraft_live_snapshot=lambda: build_control_page_minecraft_live_snapshot_runtime_deps(),
        minecraft_snapshot=lambda: build_control_page_minecraft_snapshot_runtime_deps(),
        background_tasks=lambda: build_control_page_background_tasks_runtime_deps(),
        runtime_services=lambda: build_control_page_runtime_services_runtime_deps(),
        status=lambda: build_control_page_status_runtime_deps(),
        tool=lambda: build_control_page_tool_runtime_deps(),
        search=lambda: build_control_page_search_runtime_deps(),
        text=lambda: build_control_page_text_runtime_deps(),
        input=lambda: build_control_page_input_runtime_deps(),
        server_start=lambda: build_control_page_server_start_runtime_deps(),
        build_voice_continuity_snapshot=_build_voice_barge_in_continuity_snapshot,
        cheap_tool_decision=cheap_control_page_tool_decision,
        welcome_locks=control_page_welcome_locks,
        startup_component_state=startup_component_state,
        startup_steps=STARTUP_BOOT_STEPS,
        startup_components_ready=startup_components_ready,
        discord_enabled=DISCORD_ENABLED,
        discord_ready=bot.is_ready,
        control_api_available=lambda: control_page_runner is not None,
        now=time.time,
    )
)
control_page_http_composition = ControlPageHttpComposition(
    ControlPageHttpCompositionDeps(
        docs_dir=CONTROL_PAGE_DOCS_DIR,
        assets_dir=CONTROL_PAGE_ASSETS_DIR,
        minecraft_item_icon_loader=control_page_minecraft_item_icon_loader,
        normalize_minecraft_item_name=normalize_minecraft_item_name,
        select_guild=control_page_composition.select_guild,
        build_state=build_control_page_state,
        discord_enabled=DISCORD_ENABLED,
        effective_guild_id=control_page_composition.effective_guild_id,
        append_chat_log=control_page_composition.append_chat_log,
        handle_input=control_page_composition.handle_input,
        ensure_minecraft_snapshot=control_page_composition.ensure_minecraft_snapshot,
        refresh_runtime_services=control_page_composition.get_runtime_services,
        export_memory_graph=export_memory_graph,
        memory_vault_user_snapshot=memory_vault_user_snapshot,
        memory_vault_user_note=memory_vault_user_note,
        update_memory_vault_user_note=update_memory_vault_user_note,
        local_only_mode=LOCAL_ONLY_MODE,
        port=CONTROL_PAGE_PORT,
        ensure_memory_vault_layout=ensure_memory_vault_layout,
        memory_vault_obsidian_url=memory_vault_obsidian_url,
        open_url=open_control_page_url_with_system,
        open_path=open_control_page_path_with_system,
        enabled=CONTROL_PAGE_ENABLED,
        host=CONTROL_PAGE_HOST,
        minecraft_icon_route=CONTROL_PAGE_MINECRAFT_ICON_ROUTE,
        middleware=control_page_cors_middleware,
        get_runner=lambda: control_page_runner,
        set_runner=_set_control_page_runner,
        set_site=_set_control_page_site,
        get_start_lock=lambda: control_page_start_lock,
        set_start_lock=_set_control_page_start_lock,
        lock_factory=asyncio.Lock,
        application_factory=web.Application,
        app_runner_factory=web.AppRunner,
        tcp_site_factory=web.TCPSite,
        mark_startup_component=control_page_composition.mark_startup_component,
        local_url=control_page_composition.local_url,
        log=print,
    )
)

control_page_local_url = control_page_composition.local_url
control_page_session_key = control_page_composition.session_key
control_page_effective_guild_id = control_page_composition.effective_guild_id
current_tts_target_name = control_page_composition.current_tts_target_name
get_control_page_minecraft_snapshot = control_page_composition.get_minecraft_snapshot
get_control_page_runtime_services = control_page_composition.get_runtime_services
ensure_control_page_minecraft_snapshot = control_page_composition.ensure_minecraft_snapshot
ensure_control_page_background_tasks_started = control_page_composition.ensure_background_tasks_started
stop_control_page_background_tasks = control_page_composition.stop_background_tasks
mark_startup_component = control_page_composition.mark_startup_component
startup_component_done = control_page_composition.startup_component_done
build_control_page_boot_progress = control_page_composition.build_boot_progress
start_control_page_server = control_page_composition.start_server


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


def increment_inflight_llm_requests() -> None:
    global inflight_llm_requests
    inflight_llm_requests += 1


def decrement_inflight_llm_requests() -> None:
    global inflight_llm_requests
    inflight_llm_requests = max(0, inflight_llm_requests - 1)


def build_voice_main_llm_streaming_deps() -> VoiceMainLlmStreamingDeps:
    return build_voice_main_llm_streaming_deps_from_runtime(
        model_name=MODEL_NAME,
        llm_server_url=LLM_SERVER_URL,
        main_llm_chat_content_format=MAIN_LLM_CHAT_CONTENT_FORMAT,
        voice_llm_max_tokens=VOICE_LLM_MAX_TOKENS,
        main_llm_stop_tokens=tuple(MAIN_LLM_STOP_TOKENS),
        get_http_session=get_http_session,
        is_casual_call_or_status_question=session_is_casual_call_or_status_question,
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


def build_voice_turn_entry_runtime_deps() -> VoiceTurnEntryRuntimeDeps:
    return VoiceTurnEntryRuntimeDeps(
        attach_current_task=_attach_current_task,
        detach_task=_detach_task,
        prepare_route_context=prepare_route_context,
        maybe_handle_short_circuit_route=maybe_handle_short_circuit_route,
        maybe_execute_registered_route=maybe_execute_registered_route,
        run_main_llm_turn=execute_main_llm_streaming_turn,
        emit_delivery_plan_chunks=emit_delivery_plan_chunks,
        build_answer_payload_from_text=build_answer_payload_from_text,
        build_delivery_plan=build_delivery_plan,
        split_tts_sentences=split_tts_sentences,
        record_voice_pipeline_failure=record_voice_pipeline_failure,
    )


llm_route_composition = LlmRouteComposition(
    LlmRouteCompositionDeps(
        fast_path=lambda: build_fast_path_policy_runtime_deps(),
        llm_context=lambda: build_llm_context_assembly_deps(),
        summary_json=lambda: build_summary_json_llm_runtime_deps(),
        router_json=lambda: build_router_json_llm_runtime_deps(),
        llm_route=lambda: build_llm_route_runtime_deps(),
        response_output=lambda: build_response_output_policy_runtime_deps(),
        search_answer=lambda: build_search_answer_runtime_deps(),
        search_followup=lambda: build_search_followup_runtime_deps(),
        llm_warmup=lambda: build_llm_warmup_runtime_deps(),
        main_llm=lambda: build_main_llm_runtime_deps(),
        ask_llm_once=lambda: build_ask_llm_once_runtime_deps(),
        route_executor=lambda: build_route_executor_runtime_deps(),
        voice_route_execution=lambda: build_voice_route_execution_deps(),
        voice_main_streaming=lambda: build_voice_main_llm_streaming_deps(),
        voice_turn_entry=lambda: build_voice_turn_entry_runtime_deps(),
        search_payload=search_duckduckgo_payload,
    )
)

is_control_page_source = llm_route_composition.is_control_page_source
deep_route_marker_count = llm_route_composition.deep_route_marker_count
has_negated_search_marker = llm_route_composition.has_negated_search_marker
needs_search_or_deep_routing = llm_route_composition.needs_search_or_deep_routing
is_simple_directive = llm_route_composition.is_simple_directive
is_obvious_continue = llm_route_composition.is_obvious_continue
fast_path_policy = llm_route_composition.fast_path_policy
context_policy_for_fast_path_policy = llm_route_composition.context_policy_for_fast_path_policy
prepare_llm_messages = llm_route_composition.prepare_llm_messages
extract_json_object = llm_route_composition.extract_json_object
ask_summary_llm = llm_route_composition.ask_summary_llm
ask_router_llm = llm_route_composition.ask_router_llm
classify_llm_route_async = llm_route_composition.classify_llm_route
sanitize_model_output = llm_route_composition.sanitize_model_output
extract_answer_from_reasoning = llm_route_composition.extract_answer_from_reasoning
build_search_query = llm_route_composition.build_search_query
search_duckduckgo = llm_route_composition.search_duckduckgo
answer_from_search_results = llm_route_composition.answer_from_search_results
deliver_proactive_followup = llm_route_composition.deliver_proactive_followup
schedule_search_followup_singleflight = llm_route_composition.schedule_search_followup_singleflight
run_search_followup = llm_route_composition.run_search_followup
schedule_search_followup = llm_route_composition.schedule_search_followup
warmup_llm = llm_route_composition.warmup_llm
execute_main_llm_once = llm_route_composition.execute_main_llm_once
render_tool_synthesis_recent_context = llm_route_composition.render_tool_synthesis_recent_context
tool_synthesis_answer_drifted = llm_route_composition.tool_synthesis_answer_drifted
synthesize_tool_result_with_main_llm = llm_route_composition.synthesize_tool_result_with_main_llm
resolve_promised_search_final_answer = llm_route_composition.resolve_promised_search_final_answer
ask_llm_once = llm_route_composition.ask_llm_once
resolve_route_executor = llm_route_composition.resolve_route_executor
execute_search_then_answer_action = llm_route_composition.execute_search_then_answer_action
prepare_route_context = llm_route_composition.prepare_route_context
maybe_handle_short_circuit_route = llm_route_composition.maybe_handle_short_circuit_route
maybe_execute_registered_route = llm_route_composition.maybe_execute_registered_route
execute_main_llm_streaming_turn = llm_route_composition.execute_main_llm_streaming_turn
ask_llm_streaming = llm_route_composition.ask_llm_streaming


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


def build_voice_delivery_runtime_deps() -> VoiceDeliveryRuntimeDeps:
    return VoiceDeliveryRuntimeDeps(
        attach_current_task=_attach_current_task,
        detach_task=_detach_task,
        current_turn_id=current_turn_id,
        session_topic_id=lambda session_key: session_topic_ids.get(session_key),
        new_turn_metrics=new_turn_metrics,
        is_local_speaker_voice_client=is_local_speaker_voice_client,
        start_streaming_voice_delivery=start_streaming_voice_delivery,
        start_streaming_local_voice_delivery=start_streaming_local_voice_delivery,
        ask_llm_streaming=ask_llm_streaming,
        speak_answer_local=speak_answer_local,
        local_playback_count=lambda: int(local_tts_playback_manager.snapshot().get("playCount") or 0),
        mark_barge_in_continuity_probe=_mark_voice_barge_in_continuity_probe,
        record_voice_pipeline_failure=record_voice_pipeline_failure,
        log_voice_latency=log_voice_latency,
        log_voice_stage=log_voice_stage,
        log_voice_bottleneck_summary=log_voice_bottleneck_summary,
        false_trigger_reason_code=VOICE_BARGE_IN_REASON_CODE["FALSE_TRIGGER"],
        false_trigger_reason_label=VOICE_BARGE_IN_REASON_LABEL[VOICE_BARGE_IN_REASON_CODE["FALSE_TRIGGER"]],
    )


def build_discord_text_reply_runtime_deps() -> DiscordTextReplyRuntimeDeps:
    return DiscordTextReplyRuntimeDeps(
        attach_current_task=_attach_current_task,
        detach_task=_detach_task,
        new_turn_metrics=new_turn_metrics,
        session_topic_id=lambda session_key: session_topic_ids.get(session_key),
        ask_llm_streaming=ask_llm_streaming,
        log_llm_first_chunk=lambda metrics: log_voice_latency(metrics, "llm_first_chunk_logged", "LLM 첫 chunk 시간"),
        session_state_snapshot=session_state_snapshot,
        maybe_append_proactive_question=maybe_append_proactive_question,
        update_session_state=update_session_state,
        build_answer_payload_from_text=build_answer_payload_from_text,
        format_display_text=format_display_text,
        fallback_answer_for=fallback_answer_for,
        build_delivery_plan=build_delivery_plan,
        split_tts_sentences=split_tts_sentences,
        send_discord_text=send_discord_text,
    )


# =========================================================
# 음성 입력 처리
# =========================================================
def build_voice_audio_ingress_runtime_deps() -> VoiceAudioIngressDeps:
    return VoiceAudioIngressDeps(
        voice_pipeline_state=voice_pipeline_state,
        prepare_stt_audio=prepare_stt_audio,
        save_voice_debug_audio=save_voice_debug_audio,
        room_state_snapshot=room_state_snapshot,
        session_topic_ids=session_topic_ids,
        build_topic_id=build_topic_id,
        new_turn_metrics=new_turn_metrics,
        log_voice_stage=log_voice_stage,
        register_drop_reason=register_drop_reason,
        log_voice_bottleneck_summary=log_voice_bottleneck_summary,
        downmix_int16_stereo_to_mono_float=downmix_int16_stereo_to_mono_float,
        apply_light_denoise=apply_light_denoise,
        is_transport_corrupted_audio=is_transport_corrupted_audio,
        build_voice_segment=build_voice_segment,
        compute_waveform_activity_stats=compute_waveform_activity_stats,
        estimate_voice_like_probability=estimate_voice_like_probability,
        update_room_speaker_activity=update_room_speaker_activity,
        increment_session_bad_audio=increment_session_bad_audio,
        is_tail_fragment_candidate=is_tail_fragment_candidate,
        is_probably_silent=is_probably_silent,
        print_fn=print,
        stt_use_raw_48k=STT_USE_RAW_48K,
        rate=RATE,
        channels=CHANNELS,
        target_rate=TARGET_RATE,
        voice_min_total_sec=VOICE_MIN_TOTAL_SEC,
        tail_fragment_max_raw_sec=TAIL_FRAGMENT_MAX_RAW_SEC,
        vad_enabled=VAD_ENABLED,
        voice_waveform_min_voiced_ms=VOICE_WAVEFORM_MIN_VOICED_MS,
        voice_waveform_min_run_ms=VOICE_WAVEFORM_MIN_RUN_MS,
        voice_waveform_body_rms_min=VOICE_WAVEFORM_BODY_RMS_MIN,
        voice_waveform_body_peak_min=VOICE_WAVEFORM_BODY_PEAK_MIN,
    )


def build_voice_wake_probe_runtime_deps() -> VoiceWakeProbeDeps:
    return VoiceWakeProbeDeps(
        is_room_owner_active=is_room_owner_active,
        is_session_active_for_user=is_session_active_for_user,
        pick_active_speaker=pick_active_speaker,
        log_voice_stage=log_voice_stage,
        run_blocking_stt_task=run_blocking_stt_task,
        detect_wake_word_sync=detect_wake_word_sync,
        interpret_wake_probe_result=interpret_wake_probe_result,
        clean_text=clean_text,
        apply_stt_post_corrections=apply_stt_post_corrections,
        should_require_confirm_exact_for_wake=should_require_confirm_exact_for_wake,
        apply_strict_wake_confirm_policy=apply_strict_wake_confirm_policy,
        apply_fuzzy_wake_near_miss=apply_fuzzy_wake_near_miss,
        fuzzy_leading_wake_alias=fuzzy_leading_wake_alias,
        register_drop_reason=register_drop_reason,
        log_voice_bottleneck_summary=log_voice_bottleneck_summary,
        is_likely_environment_noise=is_likely_environment_noise,
        looks_like_brief_filler_text=looks_like_brief_filler_text,
        looks_like_repetitive_noise_text=looks_like_repetitive_noise_text,
        compute_voice_band_metrics=compute_voice_band_metrics,
        save_voice_debug_audio=save_voice_debug_audio,
        increment_session_bad_audio=increment_session_bad_audio,
        should_skip_full_stt_after_wake_probe=should_skip_full_stt_after_wake_probe,
        print_fn=print,
        wake_stt_timeout_sec=WAKE_STT_TIMEOUT_SEC,
        voice_no_wake_max_continue_sec=VOICE_NO_WAKE_MAX_CONTINUE_SEC,
    )


def build_voice_tts_interrupt_gate_deps() -> VoiceTtsInterruptGateDeps:
    return VoiceTtsInterruptGateDeps(
        should_interrupt_tts=should_interrupt_tts,
        local_tts_playback_manager=local_tts_playback_manager,
        tts_playback_manager=tts_playback_manager,
        verify_speaker_for_tts_interrupt=verify_speaker_for_tts_interrupt,
        speaker_verification_allows_tts_interrupt=speaker_verification_allows_tts_interrupt,
        stop_active_tts_playback=stop_active_tts_playback,
        register_drop_reason=register_drop_reason,
        log_voice_stage=log_voice_stage,
        log_voice_bottleneck_summary=log_voice_bottleneck_summary,
        start_voice_barge_in_continuity_probe=start_voice_barge_in_continuity_probe,
        log_turn_event=log_turn_event,
        sleep=asyncio.sleep,
        monotonic=time.monotonic,
        local_only_mode=LOCAL_ONLY_MODE,
        post_tts_ignore_sec=POST_TTS_IGNORE_SEC,
        tts_interrupt_debounce_sec=TTS_INTERRUPT_DEBOUNCE_SEC,
        voice_waveform_body_rms_min=VOICE_WAVEFORM_BODY_RMS_MIN,
    )


def build_voice_stt_execution_deps() -> VoiceSttExecutionDeps:
    return VoiceSttExecutionDeps(
        run_partial_stt_flow=run_partial_stt_flow,
        run_full_stt_with_optional_rescore=run_full_stt_with_optional_rescore,
        build_partial_stt_window=build_partial_stt_window,
        get_partial_transcript=get_partial_transcript,
        read_committed_text=lambda key: session_committed_stt_text.get(key or "", ""),
        run_blocking_stt_task=run_blocking_stt_task,
        speculate_from_committed_stt=speculate_from_committed_stt,
        room_state_snapshot=room_state_snapshot,
        clean_text=clean_text,
        remember_speculative_policy=remember_speculative_policy,
        transcribe_audio=transcribe_audio16k_sync,
        choose_full_stt_candidate=choose_full_stt_candidate,
        log_voice_stage=log_voice_stage,
        mark_turn_stage=mark_turn_stage,
        save_voice_debug_audio=save_voice_debug_audio,
        print_fn=print,
        full_stt_timeout_sec=FULL_STT_TIMEOUT_SEC,
        voice_stt_max_new_tokens=VOICE_STT_MAX_NEW_TOKENS,
        rescore_enabled=STT_FULL_RESCORING_ENABLED,
        rescore_extra_tokens=STT_FULL_RESCORE_EXTRA_TOKENS,
        rescore_min_audio_sec=STT_FULL_RESCORING_MIN_AUDIO_SEC,
        rescore_min_text_len=STT_FULL_RESCORING_MIN_TEXT_LEN,
        rescore_timeout_sec=STT_FULL_RESCORING_TIMEOUT_SEC,
    )


def build_voice_transcript_finalize_deps() -> VoiceTranscriptFinalizeDeps:
    return VoiceTranscriptFinalizeDeps(
        build_final_transcript_flow=build_final_transcript_flow,
        room_state_snapshot=room_state_snapshot,
        apply_stt_post_corrections=apply_stt_post_corrections,
        clean_text=clean_text,
        set_partial_text=lambda key, value: session_partial_stt_text.__setitem__(key, value),
        commit_stable_transcript=commit_stable_transcript,
        build_transcript_result=build_transcript_result,
        speculate_from_committed_stt=speculate_from_committed_stt,
        remember_speculative_policy=remember_speculative_policy,
        room_last_voice_utterance_for_merge=room_last_voice_utterance_for_merge,
        maybe_merge_barge_in_utterance=maybe_merge_barge_in_utterance,
        log_voice_stage=log_voice_stage,
        print_fn=print,
        merge_window_sec=VOICE_BARGE_IN_MERGE_WINDOW_SEC,
        tts_interrupted_window_sec=VOICE_BARGE_IN_TTS_INTERRUPTED_WINDOW_SEC,
        incomplete_window_sec=VOICE_BARGE_IN_INCOMPLETE_UTTERANCE_WINDOW_SEC,
        complete_question_window_sec=VOICE_BARGE_IN_QUESTION_WINDOW_SEC,
        adaptive_window_enabled=VOICE_BARGE_IN_ADAPTIVE_MERGE_ENABLED,
    )


def build_voice_session_gate_deps() -> VoiceSessionGateDeps:
    return VoiceSessionGateDeps(
        is_short_followup_candidate=is_short_followup_candidate,
        should_ignore_short_transcription=should_ignore_short_transcription,
        decide_final_wake_veto=decide_final_wake_veto,
        extract_leading_wake_alias=extract_leading_wake_alias,
        register_drop_reason=register_drop_reason,
        save_voice_debug_audio=save_voice_debug_audio,
        log_voice_stage=log_voice_stage,
        log_voice_bottleneck_summary=log_voice_bottleneck_summary,
        print_fn=print,
    )


def build_voice_reply_dispatch_deps() -> VoiceReplyDispatchDeps:
    return VoiceReplyDispatchDeps(
        room_state_snapshot=room_state_snapshot,
        session_topic_ids=session_topic_ids,
        monotonic=time.monotonic,
        process_voice_reply=process_voice_reply_from_transcript_context,
        active_conversation_awaiting_reply_sec=ACTIVE_CONVERSATION_AWAITING_REPLY_SEC,
        active_conversation_voice_sec=ACTIVE_CONVERSATION_VOICE_SEC,
        canned_wake_reply=CANNED_WAKE_REPLY_TEXT,
    )


def build_voice_transcript_reply_deps(guild: Any) -> VoiceTranscriptReplyDeps:
    return VoiceTranscriptReplyDeps(
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


def build_voice_member_audio_pipeline_deps() -> VoiceMemberAudioPipelineDeps:
    return VoiceMemberAudioPipelineDeps(
        prepare_audio_ingress=prepare_voice_audio_ingress_from_runtime,
        build_audio_ingress_deps=build_voice_audio_ingress_runtime_deps,
        run_wake_probe=run_voice_wake_probe_from_runtime,
        build_wake_probe_deps=build_voice_wake_probe_runtime_deps,
        run_tts_interrupt_gate=run_voice_tts_interrupt_gate_from_runtime,
        build_tts_interrupt_gate_deps=build_voice_tts_interrupt_gate_deps,
        run_stt_execution=run_voice_stt_execution_from_runtime,
        build_stt_execution_deps=build_voice_stt_execution_deps,
        finalize_transcript=finalize_voice_transcript_from_runtime,
        build_transcript_finalize_deps=build_voice_transcript_finalize_deps,
        run_session_gate=run_voice_session_gate_from_runtime,
        build_session_gate_deps=build_voice_session_gate_deps,
        dispatch_voice_reply=dispatch_voice_reply_from_runtime,
        build_transcript_reply_deps=build_voice_transcript_reply_deps,
        build_reply_dispatch_deps=build_voice_reply_dispatch_deps,
    )


voice_io_composition = VoiceIoComposition(
    VoiceIoCompositionDeps(
        reply_side_effects=lambda: build_voice_reply_side_effect_deps(),
        reply_gate=lambda: build_voice_reply_gate_runtime_deps(),
        ingress=lambda: build_voice_ingress_runtime_deps(),
        ingress_entrypoint=lambda: build_voice_ingress_entrypoint_deps(),
        tts_interrupt=lambda: build_tts_interrupt_runtime_deps(),
        cached_tts=lambda: build_cached_tts_runtime_deps(),
        discord_tts_single=lambda: build_discord_tts_single_runtime_deps(),
        discord_tts_stream=lambda: build_discord_tts_stream_runtime_deps(),
        local_tts_single=lambda: build_local_tts_single_runtime_deps(),
        local_tts_stream=lambda: build_local_tts_stream_runtime_deps(),
        response=lambda: build_voice_response_runtime_deps(),
        stream_chunks=lambda: build_voice_stream_chunk_deps(),
        delivery=lambda: build_voice_delivery_runtime_deps(),
        text_reply=lambda: build_discord_text_reply_runtime_deps(),
        member_audio_pipeline=lambda: build_voice_member_audio_pipeline_deps(),
    )
)

finalize_voice_reply_side_effects = voice_io_composition.finalize_voice_reply_side_effects
should_reply_to_voice = voice_io_composition.should_reply_to_voice
voice_ingress_worker = voice_io_composition.voice_ingress_worker
_voice_utterance_buffer_key = voice_io_composition.voice_utterance_buffer_key
_enqueue_voice_ingress_for_processing = voice_io_composition.enqueue_voice_ingress_for_processing
_flush_voice_utterance_buffer = voice_io_composition.flush_voice_utterance_buffer
_delayed_voice_utterance_flush = voice_io_composition.delayed_voice_utterance_flush
_schedule_voice_utterance_item = voice_io_composition.schedule_voice_utterance_item
stop_active_tts_playback = voice_io_composition.stop_active_tts_playback
verify_speaker_for_tts_interrupt = voice_io_composition.verify_speaker_for_tts_interrupt
speaker_verification_allows_tts_interrupt = voice_io_composition.speaker_verification_allows_tts_interrupt
cached_audio_path_for_answer = voice_io_composition.cached_audio_path_for_answer
play_cached_answer_audio = voice_io_composition.play_cached_answer_audio
speak_answer = voice_io_composition.speak_answer
stream_tts_sentences = voice_io_composition.stream_tts_sentences
speak_answer_local = voice_io_composition.speak_answer_local
stream_local_tts_sentences = voice_io_composition.stream_local_tts_sentences
split_first_response_and_followup = voice_io_composition.split_first_response_and_followup
normalize_compare_text = voice_io_composition.normalize_compare_text
is_duplicate_followup = voice_io_composition.is_duplicate_followup
build_first_response = voice_io_composition.build_first_response
build_followup_response = voice_io_composition.build_followup_response
build_stream_speech_chunker = voice_io_composition.build_stream_speech_chunker
emit_stream_delta_chunks = voice_io_composition.emit_stream_delta_chunks
flush_streamed_answer_chunks = voice_io_composition.flush_streamed_answer_chunks
emit_delivery_plan_chunks = voice_io_composition.emit_delivery_plan_chunks
execute_voice_delivery_plan = voice_io_composition.execute_voice_delivery_plan
finalize_voice_answer = voice_io_composition.finalize_voice_answer
ask_llm_and_speak_local = voice_io_composition.ask_llm_and_speak_local
ask_llm_and_speak_streaming = voice_io_composition.ask_llm_and_speak_streaming
stream_text_reply = voice_io_composition.stream_text_reply
process_member_audio = voice_io_composition.process_member_audio
_process_member_audio_impl = voice_io_composition.process_member_audio_impl


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


is_control_command_authorized = make_control_command_authorized_checker(allowed_user_ids=ALLOWED_RESTART_USER_IDS)


def build_discord_command_session_runtime_deps() -> DiscordCommandSessionRuntimeDeps:
    return DiscordCommandSessionRuntimeDeps(
        resolve_text_thread_id=resolve_text_thread_id,
        is_text_thread_parent=lambda parent: isinstance(parent, discord.TextChannel),
        make_text_session_key=make_text_session_key,
        record_command_assistant_turn=session_state_store.record_command_assistant_turn,
        system_prompt=SYSTEM_PROMPT,
        max_history_items=MAX_HISTORY,
        normal_ttl_sec=ACTIVE_CONVERSATION_TEXT_SEC,
        question_ttl_sec=ACTIVE_CONVERSATION_TEXT_QUESTION_SEC,
    )


discord_app_composition = DiscordAppComposition(
    DiscordAppCompositionDeps(
        events=DiscordEventCompositionDeps(
            bot_user=lambda: bot.user,
            bot_guilds=lambda: list(bot.guilds),
            mark_startup_component=mark_startup_component,
            clean_text=clean_text,
            ensure_voice_worker_started=ensure_voice_worker_started,
            start_control_page_server=start_control_page_server,
            ensure_startup_components_ready=ensure_startup_components_ready,
            ensure_local_mic_service_started=ensure_local_mic_service_started,
            ensure_vision_watch_started=ensure_vision_watch_started,
            ensure_control_page_background_tasks_started=ensure_control_page_background_tasks_started,
            voice_client_type=EvelynVoiceClient,
            ensure_listening_voice_client=ensure_listening_voice_client,
            voice_rejoin_on_ready=VOICE_REJOIN_ON_READY,
            restore_last_voice_channel=restore_last_voice_channel,
            autonomy_enabled=AUTONOMY_ENABLED,
            get_or_create_autonomy_engine=get_or_create_autonomy_engine,
            text_message_handler=build_discord_text_message_handler_deps,
            log=print,
        ),
        commands=DiscordCommandCompositionDeps(
            ensure_listening_voice_client=ensure_listening_voice_client,
            mark_voice_manual_disconnect=mark_voice_manual_disconnect,
            create_task=asyncio.create_task,
            restart_bot_process=restart_bot_process,
            schedule_evelyn_stack_shutdown=schedule_evelyn_stack_shutdown,
            shutdown_bot_process=shutdown_bot_process,
            build_status_reply=build_status_command_text,
            model_name=MODEL_NAME,
            router_model_name=ROUTER_MODEL_NAME,
            summary_model_name=SUMMARY_MODEL_NAME,
            stt_model_name=STT_MODEL_NAME,
            voice_debug_save_audio=VOICE_DEBUG_SAVE_AUDIO,
            vad_enabled=VAD_ENABLED,
            vad_provider=VAD_PROVIDER,
            resolve_evelyn_page_url=resolve_evelyn_page_url,
            default_command_prefix=DEFAULT_COMMAND_PREFIX,
            get_guild_command_prefix=get_guild_command_prefix,
            save_guild_command_prefix=save_guild_command_prefix,
            build_prefix_current_reply=build_prefix_current_reply,
            build_prefix_reset_reply=build_prefix_reset_reply,
            build_prefix_saved_reply=build_prefix_saved_reply,
            guild_only_message=guild_only_command_message,
            get_or_create_autonomy_engine=get_or_create_autonomy_engine,
            autonomy_engines=autonomy_engines,
            get_routed_autonomy_executor=get_routed_autonomy_executor,
            build_autonomy_status_reply=build_autonomy_status_command_text,
            command_session=build_discord_command_session_runtime_deps,
            enable_minecraft_mode=enable_minecraft_mode,
            disable_minecraft_mode=disable_minecraft_mode,
            get_minecraft_client=get_minecraft_client,
            build_minecraft_connect_reply=build_minecraft_connect_reply,
            build_minecraft_goal_missing_reply=build_minecraft_goal_missing_reply,
            build_minecraft_goal_updated_reply=build_minecraft_goal_updated_reply,
            build_minecraft_status_reply=build_minecraft_status_command_text,
            normalize_channel_setting_action=normalize_channel_setting_action,
            get_guild_observe_channel_ids=get_guild_observe_channel_ids,
            get_guild_command_only_channel_ids=get_guild_command_only_channel_ids,
            add_guild_channel_setting=add_guild_channel_setting,
            remove_guild_channel_setting=remove_guild_channel_setting,
            build_channel_setting_list_reply=build_channel_setting_list_reply,
            build_observe_channel_usage=build_observe_channel_usage,
            build_command_channel_usage=build_command_channel_usage,
            build_help_command_text=build_help_command_text,
            is_control_command_authorized=is_control_command_authorized,
            memory_root=MEMORY_ROOT,
            reset_guild_runtime_state=reset_guild_runtime_state,
            remove_tree=shutil.rmtree,
            build_reset_guild_memory_reply=build_reset_guild_memory_reply,
            log=print,
        ),
    )
)
discord_app_bindings = discord_app_composition.register(bot)

on_ready = discord_app_bindings.on_ready
on_voice_state_update = discord_app_bindings.on_voice_state_update
on_message = discord_app_bindings.on_message
join_voice = discord_app_bindings.join_voice
rejoin_voice = discord_app_bindings.rejoin_voice
leave_voice = discord_app_bindings.leave_voice
restart_bot_command = discord_app_bindings.restart_bot_command
shutdown_bot_command = discord_app_bindings.shutdown_bot_command
status_command = discord_app_bindings.status_command
evelyn_page_command = discord_app_bindings.evelyn_page_command
set_guild_prefix = discord_app_bindings.set_guild_prefix
autonomy_start_command = discord_app_bindings.autonomy_start_command
autonomy_stop_command = discord_app_bindings.autonomy_stop_command
autonomy_status_command = discord_app_bindings.autonomy_status_command
minecraft_connect_command = discord_app_bindings.minecraft_connect_command
minecraft_disconnect_command = discord_app_bindings.minecraft_disconnect_command
minecraft_status_command = discord_app_bindings.minecraft_status_command
minecraft_goal_command = discord_app_bindings.minecraft_goal_command
observe_channel_command = discord_app_bindings.observe_channel_command
command_channel_command = discord_app_bindings.command_channel_command
help_command = discord_app_bindings.help_command
reset_guild_memory = discord_app_bindings.reset_guild_memory

restart_bot_command_error = discord_app_composition.control_command_error
shutdown_bot_command_error = discord_app_composition.control_command_error
set_guild_prefix_error = discord_app_composition.control_command_error
observe_channel_command_error = discord_app_composition.control_command_error
command_channel_command_error = discord_app_composition.control_command_error
reset_guild_memory_error = discord_app_composition.control_command_error
_mark_text_session_from_command = discord_app_composition.mark_text_session_from_command


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
