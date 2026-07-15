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
    build_autonomy_recent_context_payload,
    build_autonomy_status_payload,
    build_autonomy_summary_payload,
    build_default_autonomy_observation,
    pick_recent_user_text,
)
from evelyn_core.autonomy_router import (
    DefaultAutonomyExecutor,
    ResolveRouteExecutorRuntimeDeps,
    RoutedAutonomyExecutor,
    resolve_route_executor_from_runtime,
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
from evelyn_core.cognitive_state_runtime import CognitiveStateRuntimeDeps, update_cognitive_state_from_runtime
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
    VisionRuntimeDeps,
    build_vision_observation_prompt_from_runtime,
    build_vision_watch_prompt_from_runtime,
    format_vision_observation_from_runtime,
    vision_watch_scene_looks_bad_from_runtime,
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
    append_history_from_runtime,
    begin_user_text_turn_from_runtime,
    build_topic_id_from_runtime,
    current_turn_id_from_runtime,
    finish_assistant_text_turn_from_runtime,
    get_conversation_history_from_runtime,
    increment_session_bad_audio_from_runtime,
    is_session_active_for_user_from_runtime,
    mark_session_active_from_runtime,
    new_conversation_history_from_runtime,
    new_turn_id_from_runtime,
    next_segment_id_from_runtime,
    persona_state_hint_for_turn_from_runtime,
    remember_session_followup_target_from_runtime,
    recent_assistant_reply_summary_from_runtime,
    reset_session_bad_audio_from_runtime,
    session_state_snapshot_from_runtime,
    start_new_turn_from_runtime,
    trim_history_from_runtime,
    update_session_state_from_runtime,
)
from evelyn_core.room_speaker_activity import RoomSpeakerActivityStore
from evelyn_core.response_output_policy import (
    ResponseOutputPolicyRuntimeDeps,
    extract_json_object_from_runtime,
    fallback_for_unrequested_minecraft_leak_from_runtime,
    format_display_text_from_runtime,
    normalize_friend_style_output,
    parse_response_action_tag,
    sanitize_model_output_from_runtime,
    extract_answer_from_reasoning_from_runtime,
    sanitize_unrequested_minecraft_leak_from_runtime,
    should_label_question_response_from_runtime,
)
from evelyn_core.search_followup_policy import (
    answer_promises_search,
    strip_search_answer_sources,
)
from evelyn_core.search_tools import search_duckduckgo as search_duckduckgo_payload
from evelyn_core.runtime_status_context import (
    answer_gpu_runtime_status_query,
    compact_runtime_error,
    load_runtime_gpu_status,
    load_runtime_recent_errors,
    probe_runtime_tcp_service,
    runtime_status_port_from_url,
)
from evelyn_core.runtime_mode_policy import compute_runtime_mode_from_state, apply_runtime_mode_policy
from evelyn_core.route_fallback_policy import (
    classify_llm_route_fallback,
    normalize_route_name,
    should_force_voice_context_route,
)
from evelyn_core.fast_path_policy import (
    FastPathPolicyRuntimeDeps,
    context_policy_for_fast_path_policy_from_runtime,
    deep_route_marker_count_from_runtime,
    fast_path_policy_from_runtime,
    has_negated_search_marker_from_runtime,
    is_control_page_source_from_runtime,
    is_obvious_continue_from_runtime,
    is_simple_directive_from_runtime,
    needs_search_or_deep_routing_from_runtime,
)
from evelyn_core.tool_awareness_policy import build_tool_awareness_context
from evelyn_core.local_tool_diagnostic_context import build_local_tool_diagnostic_context
from evelyn_core.http_session_runtime import ensure_http_session_from_runtime
from evelyn_core.llm_context_assembly import LlmContextAssemblyDeps, prepare_llm_messages_from_runtime
from evelyn_core.llm_warmup_runtime import LlmWarmupRuntimeDeps, warmup_llm_from_runtime
from evelyn_core.main_llm_runtime import (
    MainLlmRuntimeDeps,
    execute_main_llm_once_from_runtime,
    render_tool_synthesis_recent_context as render_tool_synthesis_recent_context_with_deps,
    resolve_promised_search_final_answer_from_runtime,
    synthesize_tool_result_with_main_llm_from_runtime,
    tool_synthesis_answer_drifted as tool_synthesis_answer_drifted_payload,
)
from evelyn_core.search_followup_runtime import (
    build_search_query_from_runtime,
    SearchFollowupRuntimeDeps,
    deliver_proactive_followup_from_runtime,
    run_search_followup_from_runtime,
    schedule_search_followup_from_runtime,
    schedule_search_followup_singleflight_from_runtime,
)
from evelyn_core.memory_context_state import build_memory_context
from evelyn_core.startup_audio_runtime import (
    OpusStartupRuntimeDeps,
    SttWarmupRuntimeDeps,
    ensure_opus_loaded_from_runtime,
    warmup_stt_sync_from_runtime,
)
from evelyn_core.startup_component_state import (
    STARTUP_BOOT_STEPS,
    StartupComponentRuntimeDeps,
    mark_startup_component_from_runtime,
    startup_component_done_from_runtime,
)
from evelyn_core.stt_task_runtime import run_blocking_stt_task_from_runtime
from evelyn_core.stt_text_runtime import (
    build_stt_text_runtime_deps,
    build_partial_stt_window_from_runtime,
    choose_full_stt_candidate_from_runtime,
    commit_stable_transcript_from_runtime,
    detect_wake_word_sync_from_runtime,
    get_partial_transcript_from_runtime,
    longest_common_prefix_text_from_runtime,
    score_stt_candidate_from_runtime,
)
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
from evelyn_core.memory_update_runtime import MemoryUpdateRuntimeDeps, schedule_memory_update_from_runtime
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
    handle_control_command_error,
    handle_status_command,
    make_control_command_authorized_checker,
)
from evelyn_core.discord_command_session_runtime import (
    DiscordCommandSessionRuntimeDeps,
    mark_text_session_from_command_runtime,
)
from evelyn_core.discord_ingress import (
    build_voice_ingress_context,
    resolve_text_thread_id,
    normalize_voice_debug_meta,
    voice_ingress_source,
)
from evelyn_core.discord_text_turn import DiscordTextMessageHandlerDeps, handle_discord_text_message
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
from evelyn_core.discord_text_reply_runtime import (
    DiscordTextReplyRuntimeDeps,
    stream_text_reply_from_runtime,
)
from evelyn_core.discord_session_policy import (
    DiscordRoomSessionPolicy,
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
    build_local_mic_runtime_state,
    local_mic_status_line_from_payload,
    normalize_voice_input_mode,
    serialize_local_mic_runtime_state_payload,
    set_voice_input_mode_state,
    voice_input_mode_status_line_from_mode,
)
from evelyn_core.local_runtime_context import build_evelyn_runtime_dependency_context_from_payload
from evelyn_core.local_control_tts_runtime import (
    build_local_control_tts_runtime_deps,
    schedule_local_control_tts_from_runtime,
)
from evelyn_core.local_control_voice_runtime import (
    LocalControlVoiceMember,
    build_local_control_voice_member_from_runtime,
    is_local_speaker_voice_client_from_runtime,
)
from evelyn_core.local_mic_segment_runtime import (
    LocalMicDiscordSuppressionRuntimeDeps,
    LocalMicSegmentRuntimeDeps,
    LocalMicServiceRuntimeDeps,
    ensure_local_mic_service_started_from_runtime,
    handle_local_mic_segment_from_runtime,
    local_mic_effective_max_silence_ms_from_runtime,
    should_drop_discord_audio_for_local_mic_from_runtime,
    stop_local_mic_service_from_runtime,
)
from evelyn_core.tts_warmup_runtime import TtsWarmupRuntimeDeps, warmup_tts_server_from_runtime
from evelyn_core.tts_interrupt_runtime import (
    TtsInterruptRuntimeDeps,
    VoiceTtsInterruptGateDeps,
    run_voice_tts_interrupt_gate_from_runtime,
    speaker_verification_allows_tts_interrupt_from_runtime,
    stop_active_tts_playback_from_runtime,
    verify_speaker_for_tts_interrupt_from_runtime,
)
from evelyn_core.voice_timing_runtime import (
    build_voice_timing_runtime_deps as build_voice_timing_runtime_deps_from_runtime,
    VoiceTimingRuntimeDeps,
    log_voice_bottleneck_summary_from_runtime,
    log_voice_latency_from_runtime,
    log_voice_stage_from_runtime,
    should_log_voice_timing_from_runtime,
)
from evelyn_core.local_tts_playback import LocalTtsPlaybackManager
from evelyn_core.observability_metrics import (
    ModelCallMetricsStore,
    mark_turn_stage_from_runtime,
    new_turn_metrics_from_runtime,
    record_context_pipeline_benchmark_from_runtime,
    record_model_call_trace_from_runtime,
    record_turn_stage_metric,
    register_drop_reason_from_runtime,
    summarize_voice_p95_metrics,
)
from evelyn_core.omnivoice_request_runtime import (
    OmniVoiceRequestRuntimeDeps,
    build_omnivoice_tts_request_bundle_from_runtime,
    build_omnivoice_tts_result_from_runtime,
    run_omnivoice_tts_with_fallback_from_runtime,
)
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
    extract_question_policy_from_route_meta_from_runtime,
    QuestionPolicyStateRuntimeDeps,
    is_continuable_technical_topic_from_runtime,
    normalize_question_policy_mapping_from_runtime,
    apply_fast_path_question_policy_from_runtime,
    proactive_question_scope_candidates_from_runtime,
    user_frustration_with_questions_from_runtime,
    question_cooldown_hit_from_runtime,
    user_wants_direct_answer_from_runtime,
    record_question_trace_from_runtime,
    summarize_question_metrics_from_runtime,
    record_session_question_asked_from_runtime,
    resolve_pending_proactive_question_for_turn_from_runtime,
    select_and_mark_proactive_question_from_runtime,
    maybe_append_proactive_question_from_runtime,
)
from evelyn_core.assistant_contracts import (
    TtsSynthRequest,
    TtsSynthResult,
)
from evelyn_core.assistant_prompt_contract import build_evelyn_system_prompt
from evelyn_core.cached_tts_runtime import (
    CachedTtsRuntimeDeps,
    cached_audio_path_for_answer_from_runtime,
    play_cached_answer_audio_from_runtime,
)
from evelyn_core.stt_model_runtime import (
    SttModelRuntimeDeps,
    build_stt_model_runtime_deps as build_stt_model_runtime_deps_from_runtime,
    get_stt_model_from_runtime,
    normalize_stt_language_from_runtime,
)
from evelyn_core.control_page_contracts import memory_panel_reply
from evelyn_core.control_page_http import (
    add_control_page_no_store_headers,
    build_control_page_health_payload,
    control_page_cors_middleware,
    control_page_file_response,
    control_page_json_response,
    control_page_session_handler,
    resolve_control_page_asset_path,
)
from evelyn_core.control_page_server import open_path_with_system, open_url_with_system
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
from evelyn_core.control_page_guild_runtime import (
    ControlPageGuildSelectionRuntimeDeps,
    current_tts_target_name_from_runtime,
    resolve_guild_member_name_from_runtime,
    select_control_page_guild_from_runtime,
)
from evelyn_core.control_page_state_handler import ControlPageStateDeps, build_control_page_state_from_runtime
from evelyn_core.control_page_runtime_probe import probe_control_page_runtime_services
from evelyn_core.control_page_minecraft_snapshot_runtime import (
    ControlPageBackgroundTasksRuntimeDeps,
    ControlPageMinecraftSnapshotRuntimeDeps,
    ensure_control_page_background_tasks_started_from_runtime,
    ensure_control_page_minecraft_snapshot_from_runtime,
    safe_get_control_page_minecraft_snapshot_from_runtime,
    get_control_page_minecraft_snapshot_cache_copy_from_runtime,
    stop_control_page_background_tasks_from_runtime,
)
from evelyn_core.control_page_runtime_services_runtime import (
    ControlPageRuntimeServicesRuntimeDeps,
    ControlPageRuntimeServicesProbeDeps,
    probe_control_page_runtime_services_once_from_runtime,
    get_control_page_runtime_services_from_runtime,
)
from evelyn_core.control_page_status_runtime import (
    ControlPageStatusRuntimeDeps,
    build_control_page_autonomy_reply_from_runtime,
    build_control_page_inventory_reply_from_runtime,
    build_control_page_minecraft_reply_from_runtime,
    build_control_page_status_reply_from_runtime,
    build_control_page_local_status_text_from_runtime,
    build_control_page_status_text_from_runtime,
    build_control_page_voice_continuity_reply_from_runtime,
    build_control_page_voice_status_reply_from_runtime,
)
from evelyn_core.control_page_search_runtime import (
    ControlPageSearchRuntimeDeps,
    answer_control_page_search_text_from_runtime,
)
from evelyn_core.control_page_tool_runtime import (
    ControlPageInputRuntimeDeps,
    ControlPageToolRuntimeDeps,
    decide_control_page_tool_call_from_runtime,
    execute_control_page_tool_from_runtime,
    execute_control_page_memory_panel_action_from_runtime,
    execute_control_page_restart_command_from_runtime,
    handle_control_page_input_from_runtime,
    recent_control_page_history_for_router_from_runtime,
    remember_control_page_tool_turn_from_runtime,
)
from evelyn_core.control_page_ui_runtime import (
    ControlPageUiRuntimeDeps,
    append_control_page_chat_log_from_runtime,
    build_control_page_panel_state_from_runtime,
    control_page_effective_guild_id_from_runtime,
    control_page_effective_guild_name_from_runtime,
    control_page_local_url_from_runtime,
    control_page_session_key_from_runtime,
    enqueue_control_page_ui_command_from_runtime,
    get_control_page_chat_log_from_runtime,
    sanitize_control_page_welcome_text_from_runtime,
)
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
    VOICE_BARGE_IN_EVENT_FINISH,
    VOICE_BARGE_IN_REASON_CODE,
    VOICE_BARGE_IN_REASON_LABEL,
    VoiceBargeInContinuityRuntimeDeps,
    build_voice_barge_in_continuity_snapshot_from_runtime,
    format_voice_barge_in_continuity_detail_lines_from_runtime,
    format_voice_barge_in_continuity_summary_from_runtime,
    mark_voice_barge_in_continuity_probe_from_runtime,
    parse_barge_in_reason_label_from_runtime,
    reset_voice_barge_in_continuity_probe_from_runtime,
    start_voice_barge_in_continuity_probe_from_runtime,
    VoiceBargeInContinuityTracker,
)
from evelyn_core.voice_debug_audio import (
    debug_write_worker_from_runtime,
    enqueue_voice_debug_audio_from_runtime,
    ensure_debug_write_worker_started_from_runtime,
    save_voice_debug_audio_now as save_voice_debug_audio_now_payload,
)
from evelyn_core.voice_utterance import (
    UtteranceAssemblyConfig,
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
    build_voice_main_llm_streaming_deps as build_voice_main_llm_streaming_deps_from_runtime,
    maybe_execute_registered_route as maybe_execute_registered_route_with_deps,
    maybe_handle_short_circuit_route as maybe_handle_short_circuit_route_with_deps,
    prepare_route_context as prepare_route_context_with_deps,
)
from evelyn_core.voice_response_runtime import (
    VoiceResponseRuntimeDeps,
    build_first_response_from_runtime,
    build_followup_response_from_runtime,
    MainResponseGuidanceRuntimeDeps,
    build_main_response_guidance_from_runtime,
    is_duplicate_followup as is_duplicate_followup_payload,
    normalize_compare_text as normalize_compare_text_payload,
    split_first_response_and_followup as split_first_response_and_followup_with_deps,
)
from evelyn_core.voice_stream_chunks import (
    VoiceStreamChunkDeps,
    build_stream_speech_chunker_from_runtime,
    emit_delivery_plan_chunks as emit_delivery_plan_chunks_payload,
    emit_stream_delta_chunks as emit_stream_delta_chunks_payload,
    flush_streamed_answer_chunks as flush_streamed_answer_chunks_payload,
)
from evelyn_core.voice_ingress_runtime import (
    VoiceIngressEntrypointDeps,
    VoiceIngressRuntimeDeps,
    delayed_voice_utterance_flush_from_runtime,
    enqueue_voice_ingress_for_processing_from_runtime,
    flush_voice_utterance_buffer_from_runtime,
    process_member_audio_from_runtime,
    schedule_voice_utterance_item_from_runtime,
    voice_ingress_worker_from_runtime,
    voice_utterance_buffer_key as voice_utterance_buffer_key_payload,
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
from evelyn_core.voice_reply_side_effects import (
    VoiceReplySideEffectDeps,
    finalize_voice_reply_side_effects_from_runtime,
)
from evelyn_core.voice_reply_gate_runtime import (
    VoiceReplyGateRuntimeDeps,
    should_reply_to_voice_from_runtime,
)
from evelyn_core.voice_delivery_runtime import (
    VoiceDeliveryRuntimeDeps,
    ask_llm_and_speak_local_from_runtime,
    ask_llm_and_speak_streaming_from_runtime,
    finalize_voice_answer_from_runtime,
    execute_voice_delivery_plan_from_runtime,
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
    record_voice_pipeline_failure_from_runtime,
    save_last_voice_channel_state as save_last_voice_channel_state_payload,
    save_last_voice_channel_state_from_runtime,
    voice_last_channel_state_path as resolve_voice_last_channel_state_path,
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
voice_debug_counts: dict[int, int] = {}
voice_debug_stems: dict[tuple[int, str, str, str], str] = {}

tts_warmup_started = False
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


def local_control_voice_member() -> LocalControlVoiceMember:
    return build_local_control_voice_member_from_runtime(
        local_control_guild_id=LOCAL_CONTROL_GUILD_ID,
        local_control_guild_name=LOCAL_CONTROL_GUILD_NAME,
        local_mic_discord_user_ids=set(LOCAL_MIC_DISCORD_USER_IDS),
        local_mic_user_name=os.getenv("LOCAL_MIC_USER_NAME", "정훈"),
    )


def is_local_speaker_voice_client(vc: Any) -> bool:
    return is_local_speaker_voice_client_from_runtime(vc)


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
    return new_conversation_history_from_runtime(build_session_turn_runtime_deps())


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

def remember_session_followup_target(session_key: str, *, channel_id: int | None = None, message_id: int | None = None) -> None:
    remember_session_followup_target_from_runtime(
        session_key,
        channel_id=channel_id,
        message_id=message_id,
        deps=build_session_turn_runtime_deps(),
    )


def build_topic_id(*texts: str) -> str:
    return build_topic_id_from_runtime(*texts, deps=build_session_turn_runtime_deps())


def new_turn_id() -> str:
    return new_turn_id_from_runtime(build_session_turn_runtime_deps())


def current_turn_id(session_key: str | None) -> str | None:
    return current_turn_id_from_runtime(session_key, deps=build_session_turn_runtime_deps())


def next_segment_id(session_key: str | None) -> int:
    return next_segment_id_from_runtime(session_key, deps=build_session_turn_runtime_deps())


def start_new_turn(session_key: str | None, *, turn_id: str | None = None) -> str:
    return start_new_turn_from_runtime(session_key, turn_id=turn_id, deps=build_session_turn_runtime_deps())


def begin_user_text_turn(
    session_key: str,
    user_text: str,
    *,
    guild_id: int | None = None,
    user_id: int | None = None,
) -> Any:
    return begin_user_text_turn_from_runtime(
        session_key,
        user_text,
        guild_id=guild_id,
        user_id=user_id,
        deps=build_session_turn_runtime_deps(),
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
    return finish_assistant_text_turn_from_runtime(
        session_key,
        user_text,
        answer_text,
        awaiting_user_reply=awaiting_user_reply,
        topic_id=topic_id,
        guild_id=guild_id,
        user_id=user_id,
        deps=build_session_turn_runtime_deps(),
    )


def session_state_snapshot(session_key: str | None) -> dict:
    return session_state_snapshot_from_runtime(session_key, deps=build_session_turn_runtime_deps())


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
    return increment_session_bad_audio_from_runtime(session_key, deps=build_session_turn_runtime_deps())



def reset_session_bad_audio(session_key: str | None) -> None:
    reset_session_bad_audio_from_runtime(session_key, deps=build_session_turn_runtime_deps())


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
    update_session_state_from_runtime(
        session_key,
        user_id=user_id,
        speaker=speaker,
        ttl_sec=ttl_sec,
        awaiting_user_reply=awaiting_user_reply,
        topic_id=topic_id,
        answer_text=answer_text,
        user_text=user_text,
        deps=build_session_turn_runtime_deps(),
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
    mark_session_active_from_runtime(
        session_key,
        user_id=user_id,
        speaker=speaker,
        ttl_sec=ttl_sec,
        awaiting_user_reply=awaiting_user_reply,
        topic_id=topic_id,
        answer_text=answer_text,
        user_text=user_text,
        deps=build_session_turn_runtime_deps(),
    )


def is_session_active_for_user(session_key: str, user_id: int | None = None) -> bool:
    return is_session_active_for_user_from_runtime(session_key, user_id=user_id, deps=build_session_turn_runtime_deps())


def get_conversation_history(*, session_key: str | None = None, guild_id: int | None = None) -> list[dict]:
    return get_conversation_history_from_runtime(
        session_key=session_key,
        guild_id=guild_id,
        deps=build_session_turn_runtime_deps(),
    )


def trim_history(*, session_key: str | None = None, guild_id: int | None = None) -> None:
    trim_history_from_runtime(
        session_key=session_key,
        guild_id=guild_id,
        deps=build_session_turn_runtime_deps(),
    )


def append_history(session_key: str | None, user_text: str, answer: str, *, guild_id: int | None = None) -> None:
    append_history_from_runtime(
        session_key,
        user_text,
        answer,
        guild_id=guild_id,
        deps=build_session_turn_runtime_deps(),
    )


def recent_assistant_reply_summary(*, session_key: str | None = None, guild_id: int | None = None, limit: int = 1) -> str:
    return recent_assistant_reply_summary_from_runtime(
        session_key=session_key,
        guild_id=guild_id,
        limit=limit,
        deps=build_session_turn_runtime_deps(),
    )


def persona_state_hint_for_turn(user_text: str, *, session_key: str | None = None, guild_id: int | None = None) -> str:
    return persona_state_hint_for_turn_from_runtime(
        user_text,
        session_key=session_key,
        guild_id=guild_id,
        deps=build_session_turn_runtime_deps(),
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
    record_model_call_trace_from_runtime(
        model_role=model_role,
        purpose=purpose,
        hot_path=hot_path,
        started_at=started_at,
        success=success,
        monotonic=time.monotonic,
        record_model_call_metric=record_model_call_metric,
        log_turn_event=log_turn_event,
        metrics=metrics,
        first_token_ms=first_token_ms,
        error=error,
        model_name=model_name,
        endpoint=endpoint,
        turn_id=turn_id,
        session_key=session_key,
        source=source,
        guild_id=guild_id,
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
    record_context_pipeline_benchmark_from_runtime(
        metrics=metrics,
        user_text=user_text,
        answer=answer,
        source=source,
        guild_id=guild_id,
        session_key=session_key,
        now=time.time,
        benchmark_log_path=CONTEXT_PIPELINE_BENCHMARK_LOG,
        project_root=PROJECT_ROOT,
        log=print,
    )


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
    record_turn_stage_metric(turn_stage_metrics, turn_id, stage, elapsed_ms)


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
    return normalize_question_policy_mapping_from_runtime(
        value,
        default_source=default_source,
        deps=build_question_policy_runtime_deps(),
    )


def extract_question_policy_from_route_meta(route_meta: dict[str, Any] | None) -> dict[str, Any]:
    return extract_question_policy_from_route_meta_from_runtime(route_meta, deps=build_question_policy_runtime_deps())


def user_wants_direct_answer(text: str) -> bool:
    return user_wants_direct_answer_from_runtime(text, deps=build_question_policy_runtime_deps())


def user_frustration_with_questions(text: str) -> bool:
    return user_frustration_with_questions_from_runtime(text, deps=build_question_policy_runtime_deps())


def is_continuable_technical_topic(text: str) -> bool:
    return is_continuable_technical_topic_from_runtime(text, deps=build_question_policy_runtime_deps())


def question_cooldown_hit(session_key: str | None, *, now: float | None = None) -> bool:
    return question_cooldown_hit_from_runtime(
        session_key,
        now=now,
        deps=build_question_policy_state_runtime_deps(),
    )


def apply_fast_path_question_policy(
    route_decision: RouteDecision,
    *,
    user_text: str,
    session_key: str | None,
    route_meta_question_policy: dict[str, Any] | None = None,
) -> tuple[RouteDecision, bool]:
    return apply_fast_path_question_policy_from_runtime(
        route_decision,
        user_text=user_text,
        session_key=session_key,
        route_meta_question_policy=route_meta_question_policy,
        deps=build_question_policy_state_runtime_deps(),
    )


def record_question_trace(
    *,
    route_decision: RouteDecision,
    answer: str,
    shape_meta: dict[str, Any],
    metrics: dict | None,
    cooldown_hit: bool = False,
) -> None:
    record_question_trace_from_runtime(
        route_decision=route_decision,
        answer=answer,
        shape_meta=shape_meta,
        metrics=metrics,
        cooldown_hit=cooldown_hit,
        deps=build_question_policy_state_runtime_deps(),
    )


def summarize_question_metrics() -> dict[str, Any]:
    return summarize_question_metrics_from_runtime(
        deps=build_question_policy_state_runtime_deps(),
    )


def proactive_question_scope_candidates(
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> list[tuple[str, str | None]]:
    return proactive_question_scope_candidates_from_runtime(
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        deps=build_question_policy_state_runtime_deps(),
    )


def record_session_question_asked(session_key: str | None, *, now: float | None = None) -> None:
    record_session_question_asked_from_runtime(
        session_key,
        now=now,
        deps=build_question_policy_state_runtime_deps(),
    )


def resolve_pending_proactive_question_for_turn(
    guild_id: int | None,
    user_text: str,
    *,
    session_key: str | None = None,
    session_memory_key: str | None = None,
    metrics: dict | None = None,
) -> dict[str, Any]:
    return resolve_pending_proactive_question_for_turn_from_runtime(
        guild_id,
        user_text,
        session_key=session_key,
        session_memory_key=session_memory_key,
        metrics=metrics,
        deps=build_question_policy_state_runtime_deps(),
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
    return select_and_mark_proactive_question_from_runtime(
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
        deps=build_question_policy_state_runtime_deps(),
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
    return maybe_append_proactive_question_from_runtime(
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
        deps=build_question_policy_state_runtime_deps(),
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


def build_voice_barge_in_continuity_runtime_deps() -> VoiceBargeInContinuityRuntimeDeps:
    return VoiceBargeInContinuityRuntimeDeps(
        tracker=voice_barge_in_continuity_tracker,
        command_status=command_status,
    )


def _parse_barge_in_reason_label(raw_reason_code: str) -> str:
    return parse_barge_in_reason_label_from_runtime(
        raw_reason_code,
        deps=build_voice_barge_in_continuity_runtime_deps(),
    )


def _format_voice_barge_in_continuity_summary(continuity: dict[str, Any]) -> str:
    return format_voice_barge_in_continuity_summary_from_runtime(
        continuity,
        deps=build_voice_barge_in_continuity_runtime_deps(),
    )


def _format_voice_barge_in_continuity_detail_lines(continuity: dict[str, Any]) -> list[str]:
    return format_voice_barge_in_continuity_detail_lines_from_runtime(
        continuity,
        deps=build_voice_barge_in_continuity_runtime_deps(),
    )


def start_voice_barge_in_continuity_probe(metrics: dict, *, source: str) -> None:
    start_voice_barge_in_continuity_probe_from_runtime(
        metrics,
        source=source,
        deps=build_voice_barge_in_continuity_runtime_deps(),
    )


def _build_voice_barge_in_continuity_snapshot() -> dict[str, Any]:
    return build_voice_barge_in_continuity_snapshot_from_runtime(
        deps=build_voice_barge_in_continuity_runtime_deps(),
    )


def reset_voice_barge_in_continuity_probe(*, reason: str = "") -> None:
    reset_voice_barge_in_continuity_probe_from_runtime(
        reason=reason,
        deps=build_voice_barge_in_continuity_runtime_deps(),
    )


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
    mark_voice_barge_in_continuity_probe_from_runtime(
        metrics,
        success=success,
        reason=reason,
        queued_sentence_count=queued_sentence_count,
        reason_code=reason_code,
        reason_label=reason_label,
        event=event,
        deps=build_voice_barge_in_continuity_runtime_deps(),
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
    save_last_voice_channel_state_from_runtime(
        PROJECT_ROOT,
        VOICE_LAST_CHANNEL_STATE_FILE,
        voice_pipeline_state,
        guild,
        channel,
        reason=reason,
        manual_disconnect=manual_disconnect,
        log=print,
    )


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
    record_voice_pipeline_failure_from_runtime(
        voice_pipeline_counters,
        voice_pipeline_state,
        kind,
        err,
        merge_log_event_payload=merge_log_event_payload,
        log_turn_event=log_turn_event,
        metrics=metrics,
        **extra,
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
    def set_stt_cooldown_until(value: float) -> None:
        global stt_cooldown_until
        stt_cooldown_until = value

    return await run_blocking_stt_task_from_runtime(
        func,
        stage=stage,
        timeout_sec=timeout_sec,
        metrics=metrics,
        get_stt_cooldown_until=lambda: stt_cooldown_until,
        set_stt_cooldown_until=set_stt_cooldown_until,
        stt_cooldown_after_timeout_sec=STT_COOLDOWN_AFTER_TIMEOUT_SEC,
        monotonic=time.monotonic,
        get_stt_inference_lock=get_stt_inference_lock,
        increment_voice_pipeline_counter=increment_voice_pipeline_counter,
        record_voice_pipeline_failure=record_voice_pipeline_failure,
    )


def compute_runtime_mode(metrics: dict | None) -> str:
    return compute_runtime_mode_from_state(
        metrics,
        tts_backlog=tracked_tts_playback_count(tts_playback_tracker),
        inflight_llm_requests=inflight_llm_requests,
    )


def apply_runtime_mode(mode: str, opts: dict[str, Any] | None = None) -> dict[str, Any]:
    return apply_runtime_mode_policy(mode, opts)


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
    return new_turn_metrics_from_runtime(
        source=source,
        monotonic=time.monotonic,
        log_turn_event=log_turn_event,
        session_key=session_key,
        room_session_key=room_session_key,
        guild_id=guild_id,
        user_id=user_id,
        owner_user_id=owner_user_id,
        topic_id=topic_id,
        turn_id=turn_id,
        segment_id=segment_id,
        chunk_index=chunk_index,
    )


def mark_turn_stage(metrics: dict | None, key: str, *, event_name: str | None = None, **extra) -> None:
    mark_turn_stage_from_runtime(
        metrics,
        key,
        monotonic=time.monotonic,
        record_turn_stage=record_turn_stage,
        merge_log_event_payload=merge_log_event_payload,
        log_turn_event=log_turn_event,
        event_name=event_name,
        **extra,
    )


def register_drop_reason(metrics: dict | None, reason: str, **extra) -> None:
    register_drop_reason_from_runtime(
        metrics,
        reason,
        build_rejected_voice_turn=build_rejected_voice_turn,
        merge_log_event_payload=merge_log_event_payload,
        log_turn_event=log_turn_event,
        **extra,
    )


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
        max_age_days=VOICE_DEBUG_MAX_AGE_DAYS,
        max_total_bytes_per_guild=VOICE_DEBUG_MAX_TOTAL_MB_PER_GUILD * 1024 * 1024,
        preserve_newest=VOICE_DEBUG_PRESERVE_NEWEST,
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
    await debug_write_worker_from_runtime(
        queue=debug_write_queue,
        save_now=_save_voice_debug_audio_now,
        to_thread=asyncio.to_thread,
        log=print,
    )


def ensure_debug_write_worker_started() -> None:
    global debug_write_task
    debug_write_task = ensure_debug_write_worker_started_from_runtime(
        current_task=debug_write_task,
        create_task=asyncio.create_task,
        worker_coro_factory=debug_write_worker,
    )


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
    enqueue_voice_debug_audio_from_runtime(
        enabled=VOICE_DEBUG_SAVE_AUDIO,
        ensure_worker_started=ensure_debug_write_worker_started,
        queue=debug_write_queue,
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
    finalize_voice_reply_side_effects_from_runtime(
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
        deps=build_voice_reply_side_effect_deps(),
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


def is_control_page_source(source: str) -> bool:
    return is_control_page_source_from_runtime(source, deps=build_fast_path_policy_runtime_deps())


def deep_route_marker_count(text: str, *, ignore_search_markers: bool = False) -> int:
    return deep_route_marker_count_from_runtime(
        text,
        ignore_search_markers=ignore_search_markers,
        deps=build_fast_path_policy_runtime_deps(),
    )


def has_negated_search_marker(text: str) -> bool:
    return has_negated_search_marker_from_runtime(text, deps=build_fast_path_policy_runtime_deps())


def needs_search_or_deep_routing(text: str, *, source: str = "text") -> bool:
    return needs_search_or_deep_routing_from_runtime(text, source=source, deps=build_fast_path_policy_runtime_deps())


def is_simple_directive(text: str, *, source: str = "text") -> bool:
    return is_simple_directive_from_runtime(text, source=source, deps=build_fast_path_policy_runtime_deps())


def is_obvious_continue(text: str, source: str, room_state: dict | None = None) -> bool:
    return is_obvious_continue_from_runtime(
        text,
        source,
        room_state=room_state,
        deps=build_fast_path_policy_runtime_deps(),
    )


def fast_path_policy(text: str, source: str, room_state: dict | None = None) -> dict | None:
    return fast_path_policy_from_runtime(text, source, room_state=room_state, deps=build_fast_path_policy_runtime_deps())


def context_policy_for_fast_path_policy(policy: dict | None, *, source: str) -> dict[str, Any]:
    return context_policy_for_fast_path_policy_from_runtime(policy, source=source, deps=build_fast_path_policy_runtime_deps())


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
    return should_reply_to_voice_from_runtime(
        guild_id=guild_id,
        text=text,
        wake_detected=wake_detected,
        wake_match_mode=wake_match_mode,
        session_key=session_key,
        room_session_key=room_session_key,
        user_id=user_id,
        active_speaker_user_id=active_speaker_user_id,
        ignore_tts_suppression=ignore_tts_suppression,
        deps=build_voice_reply_gate_runtime_deps(),
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


async def voice_ingress_worker() -> None:
    await voice_ingress_worker_from_runtime(deps=build_voice_ingress_runtime_deps())


def ensure_voice_worker_started() -> None:
    global voice_worker_task
    ensure_debug_write_worker_started()
    if voice_worker_task is not None and not voice_worker_task.done():
        return
    voice_worker_task = asyncio.create_task(voice_ingress_worker())


def _voice_utterance_buffer_key(item: dict[str, Any]) -> str:
    return voice_utterance_buffer_key_payload(item)


async def _enqueue_voice_ingress_for_processing(item: dict[str, Any]) -> None:
    await enqueue_voice_ingress_for_processing_from_runtime(
        item,
        deps=build_voice_ingress_runtime_deps(),
    )


async def _flush_voice_utterance_buffer(key: str) -> None:
    await flush_voice_utterance_buffer_from_runtime(
        key,
        deps=build_voice_ingress_runtime_deps(),
    )


async def _delayed_voice_utterance_flush(key: str, delay_sec: float) -> None:
    await delayed_voice_utterance_flush_from_runtime(
        key,
        delay_sec,
        deps=build_voice_ingress_runtime_deps(),
    )


async def _schedule_voice_utterance_item(item: dict[str, Any]) -> None:
    await schedule_voice_utterance_item_from_runtime(
        item,
        deps=build_voice_ingress_runtime_deps(),
    )


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
    return build_vision_watch_prompt_from_runtime()


def vision_watch_scene_looks_bad(scene: str) -> bool:
    return vision_watch_scene_looks_bad_from_runtime(scene, deps=build_vision_watch_runtime_deps())


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
    return extract_json_object_from_runtime(text)


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
        result = extract_json_object_from_runtime(text)
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
        result = extract_json_object_from_runtime(text)
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
    return await update_cognitive_state_from_runtime(
        guild_id,
        user_text,
        deps=build_cognitive_state_runtime_deps(),
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        turn_scope=turn_scope,
    )

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
    return schedule_memory_update_from_runtime(
        guild_id,
        user_text,
        answer,
        deps=build_memory_update_runtime_deps(),
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        user_speaker=user_speaker,
        assistant_speaker=assistant_speaker,
        session_key=session_key,
        turn_scope=turn_scope,
        runtime_mode=runtime_mode,
    )

def sanitize_model_output(text: str) -> str:
    return sanitize_model_output_from_runtime(
        text,
        deps=build_response_output_policy_runtime_deps(),
    )


def extract_answer_from_reasoning(reasoning: str, user_text: str) -> str:
    return extract_answer_from_reasoning_from_runtime(
        reasoning,
        user_text,
        deps=build_response_output_policy_runtime_deps(),
    )


async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    http_session = ensure_http_session_from_runtime(
        http_session,
        client_timeout_factory=aiohttp.ClientTimeout,
        client_session_factory=aiohttp.ClientSession,
    )
    return http_session


def build_search_query(
    guild_id: int | None,
    user_text: str,
    *,
    session_key: str | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> str:
    return build_search_query_from_runtime(
        guild_id,
        user_text,
        session_key=session_key,
        messages=messages,
        deps=build_search_followup_runtime_deps(),
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
    await deliver_proactive_followup_from_runtime(
        guild_id,
        query,
        answer,
        deps=build_search_followup_runtime_deps(),
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
    return schedule_search_followup_singleflight_from_runtime(
        guild_id,
        query,
        deps=build_search_followup_runtime_deps(),
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
    await run_search_followup_from_runtime(
        guild_id,
        query,
        deps=build_search_followup_runtime_deps(),
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
    )


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
    schedule_search_followup_from_runtime(
        guild_id,
        session_key,
        user_text,
        answer,
        deps=build_search_followup_runtime_deps(),
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        channel_id=channel_id,
        reply_to_message_id=reply_to_message_id,
        source=source,
        force=force,
        turn_scope=turn_scope,
        runtime_mode=runtime_mode,
    )


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


def build_opus_startup_runtime_deps() -> OpusStartupRuntimeDeps:
    return OpusStartupRuntimeDeps(
        opus_is_loaded=discord_opus.is_loaded,
        load_default_opus=discord_opus._load_default,
        mark_startup_component=mark_startup_component,
        log=print,
    )


def ensure_opus_loaded() -> None:
    ensure_opus_loaded_from_runtime(deps=build_opus_startup_runtime_deps())


def build_stt_warmup_runtime_deps() -> SttWarmupRuntimeDeps:
    return SttWarmupRuntimeDeps(
        mark_startup_component=mark_startup_component,
        zeros=lambda size: np.zeros(size, dtype=np.float32),
        transcribe_audio16k_sync=transcribe_audio16k_sync,
        target_rate=TARGET_RATE,
        wake_max_tokens=WAKE_MAX_TOKENS,
        log=print,
    )


def warmup_stt_sync() -> None:
    warmup_stt_sync_from_runtime(deps=build_stt_warmup_runtime_deps())


def build_llm_warmup_runtime_deps() -> LlmWarmupRuntimeDeps:
    return LlmWarmupRuntimeDeps(
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
    )


async def warmup_llm() -> None:
    await warmup_llm_from_runtime(deps=build_llm_warmup_runtime_deps())


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
    local_mic_service = stop_local_mic_service_from_runtime(
        current_service=local_mic_service,
        local_mic_runtime_state=local_mic_runtime_state,
    )


atexit.register(stop_local_mic_service)


def resolve_evelyn_page_url() -> str | None:
    return resolve_evelyn_page_url_from_runtime(
        deps=build_evelyn_page_url_runtime_deps(
            project_root=PROJECT_ROOT,
            configured_page_url=EVELYN_PAGE_URL,
            run_git_config=subprocess.run,
        )
    )


def build_local_mic_discord_suppression_runtime_deps() -> LocalMicDiscordSuppressionRuntimeDeps:
    return LocalMicDiscordSuppressionRuntimeDeps(
        local_mic_runtime_state=local_mic_runtime_state,
        local_mic_capture_ready=lambda: bool(local_mic_service and local_mic_service.capture_ready),
        preferred_user_ids=lambda: set(LOCAL_MIC_DISCORD_USER_IDS),
        normalize_voice_input_mode=normalize_voice_input_mode,
        should_route_discord_user_to_local_mic=should_route_discord_user_to_local_mic,
        suppress_after_segment_sec=LOCAL_MIC_DISCORD_SUPPRESS_AFTER_SEGMENT_SEC,
        time=time.time,
    )


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


def build_local_mic_segment_runtime_deps() -> LocalMicSegmentRuntimeDeps:
    return LocalMicSegmentRuntimeDeps(
        local_mic_runtime_state=local_mic_runtime_state,
        normalize_voice_input_mode=normalize_voice_input_mode,
        resolve_local_mic_target=resolve_local_mic_target,
        guilds=lambda: list(bot.guilds),
        preferred_user_ids=lambda: set(LOCAL_MIC_DISCORD_USER_IDS),
        local_only_mode=LOCAL_ONLY_MODE,
        local_control_voice_member=local_control_voice_member,
        process_member_audio=process_member_audio,
        log=print,
        time=time.time,
    )


async def handle_local_mic_segment(pcm_bytes: bytes, debug_meta: dict[str, Any] | None = None) -> None:
    await handle_local_mic_segment_from_runtime(
        pcm_bytes,
        debug_meta,
        deps=build_local_mic_segment_runtime_deps(),
    )


def build_local_mic_service_runtime_deps() -> LocalMicServiceRuntimeDeps:
    return LocalMicServiceRuntimeDeps(
        local_mic_runtime_state=local_mic_runtime_state,
        local_mic_enabled=LOCAL_MIC_ENABLED,
        local_only_mode=LOCAL_ONLY_MODE,
        discord_user_ids=lambda: set(LOCAL_MIC_DISCORD_USER_IDS),
        service_factory=LocalMicCaptureService,
        get_running_loop=asyncio.get_running_loop,
        create_task=asyncio.create_task,
        handle_local_mic_segment=handle_local_mic_segment,
        max_silence_ms_provider=lambda: local_mic_effective_max_silence_ms_from_runtime(
            local_tts_playback_snapshot=local_tts_playback_manager.snapshot,
            tts_active_max_silence_ms=LOCAL_MIC_TTS_ACTIVE_MAX_SILENCE_MS,
            default_max_silence_ms=LOCAL_MIC_MAX_SILENCE_MS,
        ),
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
        vad_filter_enabled=LOCAL_MIC_VAD_FILTER_ENABLED,
        env_noise_filter_enabled=LOCAL_MIC_ENV_NOISE_FILTER_ENABLED,
        waveform_filter_enabled=LOCAL_MIC_WAVEFORM_FILTER_ENABLED,
        log=print,
    )


async def ensure_local_mic_service_started() -> None:
    global local_mic_service
    local_mic_service = await ensure_local_mic_service_started_from_runtime(
        current_service=local_mic_service,
        deps=build_local_mic_service_runtime_deps(),
    )


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


async def warmup_tts_server() -> None:
    global tts_warmup_started

    tts_warmup_started = True
    await warmup_tts_server_from_runtime(deps=build_tts_warmup_runtime_deps())


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


def should_log_voice_timing(elapsed_ms: float) -> bool:
    return should_log_voice_timing_from_runtime(elapsed_ms, deps=build_voice_timing_runtime_deps())


def log_voice_latency(metrics: dict | None, key: str, label: str) -> None:
    log_voice_latency_from_runtime(metrics, key, label, deps=build_voice_timing_runtime_deps())


def log_voice_stage(metrics: dict | None, label: str, *, extra: str = "", key: str | None = None) -> None:
    log_voice_stage_from_runtime(
        metrics,
        label,
        deps=build_voice_timing_runtime_deps(),
        extra=extra,
        key=key,
    )


def log_voice_bottleneck_summary(
    metrics: dict | None,
    *,
    label: str,
    extra: str = "",
    event_name: str = "turn_summary",
) -> None:
    log_voice_bottleneck_summary_from_runtime(
        metrics,
        deps=build_voice_timing_runtime_deps(),
        label=label,
        extra=extra,
        event_name=event_name,
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
            request_bundle = build_omnivoice_tts_request_bundle_from_runtime(
                text=text,
                voice_name=voice_name,
                deps=build_omnivoice_request_runtime_deps(),
                turn_id=turn_id,
                chunk_index=chunk_index,
                session_key=session_key,
            )
            tts_request = request_bundle.request
            payload = request_bundle.payload

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
                    return build_omnivoice_tts_result_from_runtime(
                        tts_request,
                        deps=build_omnivoice_request_runtime_deps(),
                        ok=False,
                        status_code=resp.status,
                        latency_ms=(time.monotonic() - request_started_mono) * 1000.0,
                        first_audio_ms=first_audio_ms,
                        error_code="http_error",
                        error_text=error_text,
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
                    return build_omnivoice_tts_result_from_runtime(
                        tts_request,
                        deps=build_omnivoice_request_runtime_deps(),
                        ok=False,
                        status_code=resp.status,
                        latency_ms=(time.monotonic() - request_started_mono) * 1000.0,
                        first_audio_ms=first_audio_ms,
                        error_code="empty_audio",
                        error_text="OmniVoice returned no PCM bytes.",
                    )
                return build_omnivoice_tts_result_from_runtime(
                    tts_request,
                    deps=build_omnivoice_request_runtime_deps(),
                    ok=True,
                    status_code=resp.status,
                    latency_ms=(time.monotonic() - request_started_mono) * 1000.0,
                    first_audio_ms=first_audio_ms,
                )

        try:
            await run_omnivoice_tts_with_fallback_from_runtime(
                primary_voice=OMNIVOICE_VOICE,
                stream_with_voice=stream_with_voice,
                log=print,
            )
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


def transcribe_audio16k_sync(audio16k: np.ndarray, max_new_tokens: int = 256, *, sampling_rate: int = TARGET_RATE, stage: str = "full") -> str:
    if audio16k.size == 0:
        return ""

    effective_rate = max(1, int(sampling_rate))
    print(f"[STT INPUT][{stage}] sampling_rate={effective_rate} samples={audio16k.size} sec={audio16k.size / float(effective_rate):.2f}")
    if STT_SERVICE_URL:
        try:
            language = (
                normalize_stt_language_from_runtime(None, default_language=STT_LANGUAGE)
                if STT_FORCE_LANGUAGE
                else None
            )
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

    language = (
        normalize_stt_language_from_runtime(None, default_language=STT_LANGUAGE)
        if STT_FORCE_LANGUAGE
        else None
    )
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
    return build_partial_stt_window_from_runtime(audio16k, sampling_rate=sampling_rate)


def longest_common_prefix_text(a: str, b: str) -> str:
    return longest_common_prefix_text_from_runtime(a, b, clean_text=clean_text)


def commit_stable_transcript(session_key: str | None, *, new_partial_text: str) -> str:
    return commit_stable_transcript_from_runtime(
        session_key,
        new_partial_text=new_partial_text,
        deps=_build_stt_text_runtime_deps(),
    )


def get_partial_transcript(session_key: str | None, audio16k: np.ndarray, *, sampling_rate: int = TARGET_RATE) -> tuple[str, str]:
    return get_partial_transcript_from_runtime(
        session_key,
        audio16k,
        sampling_rate=sampling_rate,
        max_new_tokens=max(64, min(VOICE_STT_MAX_NEW_TOKENS, 128)),
        transcribe_audio16k_sync=transcribe_audio16k_sync,
        deps=_build_stt_text_runtime_deps(),
    )


def score_stt_candidate(text: str, *, wake_probe: str = "") -> float:
    return score_stt_candidate_from_runtime(
        text,
        wake_probe=wake_probe,
        deps=_build_stt_text_runtime_deps(),
    )


def choose_full_stt_candidate(primary_text: str, rescore_text: str, *, wake_probe: str = "") -> tuple[str, dict]:
    return choose_full_stt_candidate_from_runtime(
        primary_text,
        rescore_text,
        wake_probe=wake_probe,
        deps=_build_stt_text_runtime_deps(),
    )


def detect_wake_word_sync(audio: np.ndarray, *, sampling_rate: int = TARGET_RATE) -> dict[str, str | bool | None]:
    return detect_wake_word_sync_from_runtime(
        audio,
        sampling_rate=sampling_rate,
        wake_audio_sec=WAKE_AUDIO_SEC,
        wake_confirm_audio_sec=WAKE_CONFIRM_AUDIO_SEC,
        wake_max_tokens=WAKE_MAX_TOKENS,
        wake_confirm_max_tokens=WAKE_CONFIRM_MAX_TOKENS,
        transcribe_audio16k_sync=transcribe_audio16k_sync,
        apply_stt_post_corrections=apply_stt_post_corrections,
        strip_leading_voice_fillers=strip_leading_voice_fillers,
        extract_leading_wake_alias=extract_leading_wake_alias,
        fuzzy_leading_wake_alias=fuzzy_leading_wake_alias,
        looks_like_gibberish_probe=looks_like_gibberish_probe,
        slice_audio_window=slice_audio_window,
    )


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


async def stop_active_tts_playback(guild_id: int | None, *, reason: str = "interrupt") -> bool:
    return await stop_active_tts_playback_from_runtime(
        guild_id,
        deps=build_tts_interrupt_runtime_deps(),
        reason=reason,
    )


async def verify_speaker_for_tts_interrupt(
    audio: np.ndarray,
    *,
    sampling_rate: int,
    source: str | None,
    metrics: dict | None = None,
) -> SpeakerVerificationResult:
    return await verify_speaker_for_tts_interrupt_from_runtime(
        audio,
        deps=build_tts_interrupt_runtime_deps(),
        sampling_rate=sampling_rate,
        source=source,
        metrics=metrics,
    )


def speaker_verification_allows_tts_interrupt(result: SpeakerVerificationResult) -> bool:
    return speaker_verification_allows_tts_interrupt_from_runtime(result)


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


def cached_audio_path_for_answer(answer: str) -> Path | None:
    return cached_audio_path_for_answer_from_runtime(answer, deps=build_cached_tts_runtime_deps())


async def play_cached_answer_audio(
    vc: discord.VoiceClient,
    answer: str,
    *,
    turn_id: str | None = None,
    session_key: str | None = None,
    metrics: dict | None = None,
) -> bool:
    return await play_cached_answer_audio_from_runtime(
        vc,
        answer,
        deps=build_cached_tts_runtime_deps(),
        turn_id=turn_id,
        session_key=session_key,
        metrics=metrics,
    )


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


def split_first_response_and_followup(answer: str) -> tuple[str, str]:
    return split_first_response_and_followup_with_deps(answer, deps=build_voice_response_runtime_deps())


def normalize_compare_text(text: str) -> str:
    return normalize_compare_text_payload(text)


def is_duplicate_followup(first_response: str, followup_text: str) -> bool:
    return is_duplicate_followup_payload(first_response, followup_text)


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
    return await build_first_response_from_runtime(
        user_text,
        deps=build_voice_response_runtime_deps(),
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        debug_text=debug_text,
        metrics=metrics,
    )


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
    return await build_followup_response_from_runtime(
        user_text,
        first_response,
        deps=build_voice_response_runtime_deps(),
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        debug_text=debug_text,
        metrics=metrics,
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


async def execute_main_llm_once(
    *,
    payload: dict[str, Any],
    user_text: str,
) -> tuple[str, str]:
    return await execute_main_llm_once_from_runtime(
        deps=build_main_llm_runtime_deps(),
        payload=payload,
        user_text=user_text,
    )


def render_tool_synthesis_recent_context(
    messages: list[dict[str, Any]] | None,
    *,
    user_text: str,
    max_items: int = 6,
    max_chars: int = 900,
) -> str:
    return render_tool_synthesis_recent_context_with_deps(
        messages,
        deps=build_main_llm_runtime_deps(),
        user_text=user_text,
        max_items=max_items,
        max_chars=max_chars,
    )


def tool_synthesis_answer_drifted(answer: str, *, user_text: str, tool_result_text: str) -> bool:
    return tool_synthesis_answer_drifted_payload(
        answer,
        user_text=user_text,
        tool_result_text=tool_result_text,
    )


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
    return await synthesize_tool_result_with_main_llm_from_runtime(
        deps=build_main_llm_runtime_deps(),
        user_text=user_text,
        tool_name=tool_name,
        tool_result_text=tool_result_text,
        guild_id=guild_id,
        session_key=session_key,
        source=source,
        messages=messages,
        cognitive_state=cognitive_state,
        route_decision=route_decision,
        metrics=metrics,
    )


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
    return await resolve_promised_search_final_answer_from_runtime(
        deps=build_main_llm_runtime_deps(),
        user_text=user_text,
        answer_text=answer_text,
        guild_id=guild_id,
        session_key=session_key,
        source=source,
        messages=messages,
        cognitive_state=cognitive_state,
        route_decision=route_decision,
        metrics=metrics,
    )

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
    lightweight_persona_turn = session_is_casual_call_or_status_question(guided_user_text)
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


def build_voice_stream_chunk_deps() -> VoiceStreamChunkDeps:
    return VoiceStreamChunkDeps(
        tts_first_chunk_min_chars=TTS_FIRST_CHUNK_MIN_CHARS,
        tts_first_chunk_target_chars=TTS_FIRST_CHUNK_TARGET_CHARS,
        tts_first_chunk_max_chars=TTS_FIRST_CHUNK_MAX_CHARS,
        tts_next_chunk_min_chars=TTS_NEXT_CHUNK_MIN_CHARS,
        tts_next_chunk_target_chars=TTS_NEXT_CHUNK_TARGET_CHARS,
        tts_next_chunk_max_chars=TTS_NEXT_CHUNK_MAX_CHARS,
    )


def build_stream_speech_chunker(*, metrics: dict | None) -> SpeechChunker:
    return build_stream_speech_chunker_from_runtime(metrics=metrics, deps=build_voice_stream_chunk_deps())


async def emit_stream_delta_chunks(
    delta_text: str,
    *,
    speech_chunker: SpeechChunker,
    on_sentence: Callable[[str], Awaitable[None]] | None,
    question_stream_state: dict[str, int] | None = None,
) -> bool:
    return await emit_stream_delta_chunks_payload(
        delta_text,
        speech_chunker=speech_chunker,
        on_sentence=on_sentence,
        question_stream_state=question_stream_state,
    )


async def flush_streamed_answer_chunks(
    answer: str,
    *,
    speech_chunker: SpeechChunker,
    on_sentence: Callable[[str], Awaitable[None]] | None,
    emitted_any: bool,
    question_stream_state: dict[str, int] | None = None,
) -> None:
    await flush_streamed_answer_chunks_payload(
        answer,
        speech_chunker=speech_chunker,
        on_sentence=on_sentence,
        emitted_any=emitted_any,
        question_stream_state=question_stream_state,
    )


async def emit_delivery_plan_chunks(
    delivery_plan: DeliveryPlan,
    *,
    on_sentence: Callable[[str], Awaitable[None]] | None,
) -> None:
    await emit_delivery_plan_chunks_payload(delivery_plan, on_sentence=on_sentence)


DEFAULT_INTERNAL_ROUTES = {"main_direct", "policy_short_circuit", "search_executor", "routing", "delivery"}
DISABLED_MAIN_APP_SKILL_ROUTES = {"minecraft"}


def build_route_executor_runtime_deps() -> ResolveRouteExecutorRuntimeDeps:
    return ResolveRouteExecutorRuntimeDeps(
        get_autonomy_engine=lambda guild_id: autonomy_engines.get(guild_id),
        create_autonomy_engine=get_or_create_autonomy_engine,
    )


def resolve_route_executor(*, guild_id: int | None, route_name: str) -> Any:
    return resolve_route_executor_from_runtime(
        guild_id,
        route_name,
        deps=build_route_executor_runtime_deps(),
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


def enqueue_control_page_ui_command(action: str, *, panel_id: str | None = None) -> dict[str, Any]:
    return enqueue_control_page_ui_command_from_runtime(
        action,
        panel_id=panel_id,
        deps=build_control_page_ui_runtime_deps(),
    )


def build_control_page_panel_state() -> dict[str, Any]:
    return build_control_page_panel_state_from_runtime(deps=build_control_page_ui_runtime_deps())


def control_page_local_url() -> str:
    return control_page_local_url_from_runtime(deps=build_control_page_ui_runtime_deps())


def control_page_session_key(guild_id: int | None) -> str:
    return control_page_session_key_from_runtime(guild_id, deps=build_control_page_ui_runtime_deps())


def control_page_effective_guild_id(guild: discord.Guild | None) -> int:
    return control_page_effective_guild_id_from_runtime(guild, deps=build_control_page_ui_runtime_deps())


def control_page_effective_guild_name(guild: discord.Guild | None) -> str:
    return control_page_effective_guild_name_from_runtime(guild, deps=build_control_page_ui_runtime_deps())


def append_control_page_chat_log(guild_id: int, role: str, author: str, text: str) -> None:
    append_control_page_chat_log_from_runtime(
        guild_id,
        role,
        author,
        text,
        deps=build_control_page_ui_runtime_deps(),
    )


def get_control_page_chat_log(guild_id: int) -> list[dict[str, Any]]:
    return get_control_page_chat_log_from_runtime(guild_id, deps=build_control_page_ui_runtime_deps())


def sanitize_control_page_welcome_text(text: str) -> str:
    return sanitize_control_page_welcome_text_from_runtime(text, deps=build_control_page_ui_runtime_deps())


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
    return select_control_page_guild_from_runtime(requested_guild_id, deps=build_control_page_guild_selection_runtime_deps())


def resolve_guild_member_name(guild: discord.Guild | None, user_id: int | None) -> str:
    return resolve_guild_member_name_from_runtime(
        guild,
        user_id,
        deps=build_control_page_guild_selection_runtime_deps(),
    )


def current_tts_target_name(guild: discord.Guild | None) -> str:
    return current_tts_target_name_from_runtime(guild, deps=build_control_page_guild_selection_runtime_deps())


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
    return await safe_get_control_page_minecraft_snapshot_from_runtime(
        guild_id,
        timeout_seconds=timeout_seconds,
        deps=build_control_page_minecraft_snapshot_runtime_deps(),
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


async def get_control_page_runtime_services(*, force: bool = False) -> dict[str, Any]:
    return await get_control_page_runtime_services_from_runtime(
        deps=build_control_page_runtime_services_runtime_deps(),
        force=force,
    )


def get_control_page_minecraft_snapshot_cache_copy() -> dict[str, Any]:
    return get_control_page_minecraft_snapshot_cache_copy_from_runtime(
        deps=build_control_page_minecraft_snapshot_runtime_deps(),
    )


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
        select_control_page_guild=select_control_page_guild,
        ensure_minecraft_snapshot=ensure_control_page_minecraft_snapshot,
        sleep=asyncio.sleep,
        log=print,
        refresh_interval_sec=CONTROL_PAGE_MINECRAFT_CACHE_REFRESH_SEC,
    )


def _set_control_page_minecraft_snapshot_poll_task(task: asyncio.Task | None) -> None:
    global control_page_minecraft_snapshot_poll_task
    control_page_minecraft_snapshot_poll_task = task


async def ensure_control_page_minecraft_snapshot(
    guild_id: int | None,
    *,
    force: bool = False,
    wait: bool = False,
) -> dict[str, Any]:
    return await ensure_control_page_minecraft_snapshot_from_runtime(
        guild_id,
        deps=build_control_page_minecraft_snapshot_runtime_deps(),
        force=force,
        wait=wait,
    )


async def ensure_control_page_background_tasks_started() -> None:
    await ensure_control_page_background_tasks_started_from_runtime(
        deps=build_control_page_background_tasks_runtime_deps(),
    )


def stop_control_page_background_tasks() -> None:
    stop_control_page_background_tasks_from_runtime(
        deps=build_control_page_background_tasks_runtime_deps(),
    )


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
        get_control_page_minecraft_snapshot=safe_get_control_page_minecraft_snapshot,
        build_control_page_inventory_reply_payload=build_control_page_inventory_reply_payload,
        build_control_page_minecraft_reply_payload=build_control_page_minecraft_reply_payload,
        get_autonomy_engine=autonomy_engines.get,
        get_routed_autonomy_executor=get_routed_autonomy_executor,
        build_control_page_autonomy_reply_payload=build_control_page_autonomy_reply_payload,
    )


def build_control_page_status_text(guild: discord.Guild, minecraft: dict[str, Any]) -> str:
    return build_control_page_status_text_from_runtime(
        guild,
        minecraft,
        deps=build_control_page_status_runtime_deps(),
    )


def build_control_page_local_status_text(runtime_services: dict[str, Any] | None = None) -> str:
    return build_control_page_local_status_text_from_runtime(
        runtime_services,
        deps=build_control_page_status_runtime_deps(),
    )


async def build_control_page_status_reply(guild: discord.Guild) -> str:
    return await build_control_page_status_reply_from_runtime(
        guild,
        deps=build_control_page_status_runtime_deps(),
    )


def build_control_page_voice_status_reply(guild: discord.Guild | None) -> str:
    return build_control_page_voice_status_reply_from_runtime(
        guild,
        deps=build_control_page_status_runtime_deps(),
    )


def build_control_page_voice_continuity_reply(guild: discord.Guild | None) -> str:
    _ = guild
    continuity = _build_voice_barge_in_continuity_snapshot()
    return build_control_page_voice_continuity_reply_from_runtime(
        continuity,
        deps=build_control_page_status_runtime_deps(),
    )


async def build_control_page_inventory_reply(guild: discord.Guild) -> str:
    return await build_control_page_inventory_reply_from_runtime(
        guild,
        deps=build_control_page_status_runtime_deps(),
    )


async def build_control_page_minecraft_reply(guild: discord.Guild) -> str:
    return await build_control_page_minecraft_reply_from_runtime(
        guild,
        deps=build_control_page_status_runtime_deps(),
    )


def build_control_page_autonomy_reply(guild: discord.Guild) -> str:
    return build_control_page_autonomy_reply_from_runtime(
        guild,
        deps=build_control_page_status_runtime_deps(),
    )


def build_control_page_tool_runtime_deps() -> ControlPageToolRuntimeDeps:
    return ControlPageToolRuntimeDeps(
        clean_text=clean_text,
        enqueue_control_page_ui_command=enqueue_control_page_ui_command,
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
            "build_local_status_text": build_control_page_local_status_text,
            "build_status_reply": build_control_page_status_reply,
            "schedule_local_shutdown": schedule_evelyn_local_shutdown,
            "schedule_stack_shutdown": schedule_evelyn_stack_shutdown,
            "schedule_bot_shutdown": lambda: asyncio.create_task(shutdown_bot_process()),
            "build_autonomy_reply": build_control_page_autonomy_reply,
            "build_voice_status_reply": build_control_page_voice_status_reply,
            "set_input_mode": set_voice_input_mode,
            "input_mode_status_line": voice_input_mode_status_line,
            "restore_voice_channel": restore_last_voice_channel,
            "build_voice_continuity_reply": build_control_page_voice_continuity_reply,
            "reset_continuity_probe": reset_voice_barge_in_continuity_probe,
            "build_inventory_reply": build_control_page_inventory_reply,
            "build_minecraft_reply": build_control_page_minecraft_reply,
            "enable_mode": enable_minecraft_mode,
            "disable_mode": disable_minecraft_mode,
            "get_client": get_minecraft_client,
            "format_position": format_position_short,
        },
    )


def execute_control_page_memory_panel_action(action: str) -> str:
    return execute_control_page_memory_panel_action_from_runtime(
        action,
        deps=build_control_page_tool_runtime_deps(),
    )


def execute_control_page_restart_command() -> str:
    return execute_control_page_restart_command_from_runtime(deps=build_control_page_tool_runtime_deps())


def recent_control_page_history_for_router(*, session_key: str, guild_id: int | None, limit: int = 6) -> str:
    return recent_control_page_history_for_router_from_runtime(
        session_key=session_key,
        guild_id=guild_id,
        limit=limit,
        deps=build_control_page_tool_runtime_deps(),
    )


def remember_control_page_tool_turn(
    guild: discord.Guild | None,
    user_text: str,
    reply_text: str,
    decision: dict[str, Any],
) -> None:
    remember_control_page_tool_turn_from_runtime(
        guild,
        user_text,
        reply_text,
        decision,
        deps=build_control_page_tool_runtime_deps(),
    )


async def decide_control_page_tool_call(text: str, *, guild_id: int | None, session_key: str) -> dict[str, Any] | None:
    return await decide_control_page_tool_call_from_runtime(
        text,
        guild_id=guild_id,
        session_key=session_key,
        deps=build_control_page_tool_runtime_deps(),
    )


async def decide_control_page_ui_tool_call(text: str, *, guild_id: int | None, session_key: str) -> dict[str, Any] | None:
    return await decide_control_page_tool_call(text, guild_id=guild_id, session_key=session_key)


async def execute_control_page_tool(guild: discord.Guild | None, decision: dict[str, Any]) -> str:
    return await execute_control_page_tool_from_runtime(
        guild,
        decision,
        deps=build_control_page_tool_runtime_deps(),
    )


async def execute_control_page_command(guild: discord.Guild | None, text: str) -> str:
    decision = cheap_control_page_tool_decision(text)
    if decision is not None:
        return await execute_control_page_tool(guild, decision)
    return "지원하지 않는 명령어야. /help 로 현재 페이지 명령어를 확인해줘."


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


async def answer_control_page_search_text(guild: discord.Guild | None, user_text: str) -> str:
    return await answer_control_page_search_text_from_runtime(
        guild,
        user_text,
        deps=build_control_page_search_runtime_deps(),
    )


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


def build_control_page_input_runtime_deps() -> ControlPageInputRuntimeDeps:
    return ControlPageInputRuntimeDeps(
        clean_text=clean_text,
        control_page_effective_guild_id=control_page_effective_guild_id,
        control_page_session_key=control_page_session_key,
        cheap_control_page_tool_decision=cheap_control_page_tool_decision,
        execute_control_page_tool=execute_control_page_tool,
        remember_control_page_tool_turn=remember_control_page_tool_turn,
        should_route_control_page_tool_candidate=should_route_control_page_tool_candidate,
        decide_control_page_tool_call=decide_control_page_tool_call,
        control_page_tool_decision_from_llm=control_page_tool_decision_from_llm,
        control_page_tool_policy_error=control_page_tool_policy_error,
        control_page_tool_reply_from_execution=control_page_tool_reply_from_execution,
        should_force_search_query=should_force_search_query,
        answer_control_page_search_text=answer_control_page_search_text,
        answer_control_page_text=answer_control_page_text,
    )


async def handle_control_page_input(guild: discord.Guild | None, text: str) -> str:
    return await handle_control_page_input_from_runtime(
        guild,
        text,
        deps=build_control_page_input_runtime_deps(),
    )


def mark_startup_component(key: str, status: str, detail: str = "") -> None:
    mark_startup_component_from_runtime(
        key,
        status,
        detail,
        deps=StartupComponentRuntimeDeps(
            startup_component_state=startup_component_state,
            now=time.time,
        ),
    )


def startup_component_done(key: str) -> bool:
    return startup_component_done_from_runtime(
        key,
        deps=StartupComponentRuntimeDeps(
            startup_component_state=startup_component_state,
            now=time.time,
        ),
    )


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


open_control_page_path_with_system = open_path_with_system
open_control_page_url_with_system = open_url_with_system


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
        app.router.add_get("/api/control-page/session", control_page_session_handler)
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
    return await execute_voice_delivery_plan_from_runtime(
        vc,
        delivery_plan,
        deps=build_voice_delivery_runtime_deps(),
        metrics=metrics,
        turn_id=turn_id,
        session_key=session_key,
        turn_scope=turn_scope,
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


async def finalize_voice_answer(
    answer: str,
    *,
    on_final_answer: Callable[[str], Awaitable[None]] | None,
    delivery: StreamingVoiceDelivery,
    metrics: dict,
) -> tuple[str, int]:
    return await finalize_voice_answer_from_runtime(
        answer,
        on_final_answer=on_final_answer,
        delivery=delivery,
        metrics=metrics,
        deps=build_voice_delivery_runtime_deps(),
    )


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
    return await ask_llm_and_speak_local_from_runtime(
        _vc,
        user_text,
        deps=build_voice_delivery_runtime_deps(),
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
    return await ask_llm_and_speak_streaming_from_runtime(
        vc,
        user_text,
        deps=build_voice_delivery_runtime_deps(),
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
    return await stream_text_reply_from_runtime(
        channel,
        user_text,
        guild_id=guild_id,
        session_key=session_key,
        turn_id=turn_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        debug_text=debug_text,
        include_voice=include_voice,
        turn_scope=turn_scope,
        proactive_resolution=proactive_resolution,
        deps=build_discord_text_reply_runtime_deps(),
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


async def process_member_audio(member: discord.Member | None, pcm_bytes: bytes, debug_meta: dict | None = None) -> None:
    await process_member_audio_from_runtime(
        member=member,
        pcm_bytes=pcm_bytes,
        debug_meta=debug_meta,
        deps=build_voice_ingress_entrypoint_deps(),
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
    ingress = prepare_voice_audio_ingress_from_runtime(
        member,
        pcm_bytes,
        debug_meta,
        session_key=session_key,
        room_session_key=room_session_key,
        turn_id=turn_id,
        segment_id=segment_id,
        ingress_during_reply=ingress_during_reply,
        owner_user_id_on_ingress=owner_user_id_on_ingress,
        deps=build_voice_audio_ingress_runtime_deps(),
    )
    if ingress is None:
        return

    guild = ingress.guild
    guild_id = ingress.guild_id
    speaker_name = ingress.speaker_name
    owner_user_id = ingress.owner_user_id
    metrics = ingress.metrics
    audio16k = ingress.audio16k
    audio_for_wake = ingress.audio_for_wake
    stt_sampling_rate = ingress.stt_sampling_rate
    wake_sampling_rate = ingress.wake_sampling_rate
    raw_seconds = ingress.raw_seconds
    duration_sec = ingress.duration_sec
    voice_segment = ingress.voice_segment
    voiced_ms = ingress.voiced_ms
    body_rms = ingress.body_rms
    voice_like_prob = ingress.voice_like_prob

    wake = await run_voice_wake_probe_from_runtime(
        member=member,
        pcm_bytes=pcm_bytes,
        debug_meta=debug_meta,
        session_key=session_key,
        room_session_key=room_session_key,
        owner_user_id=owner_user_id,
        guild_id=guild_id,
        speaker_name=speaker_name,
        audio16k=audio16k,
        audio_for_wake=audio_for_wake,
        wake_sampling_rate=wake_sampling_rate,
        raw_seconds=raw_seconds,
        duration_sec=duration_sec,
        metrics=metrics,
        deps=build_voice_wake_probe_runtime_deps(),
    )
    if wake is None:
        return

    owner_followup_active = wake.owner_followup_active
    active_speaker_user_id = wake.active_speaker_user_id
    wake_probe = wake.wake_probe
    wake_confirm = wake.wake_confirm
    wake_detected = wake.wake_detected
    wake_match_mode = wake.wake_match_mode
    wake_alias = wake.wake_alias
    wake_reject_reason = wake.wake_reject_reason

    interrupt_gate = await run_voice_tts_interrupt_gate_from_runtime(
        member=member,
        guild_id=guild_id,
        session_key=session_key,
        room_session_key=room_session_key,
        owner_user_id=owner_user_id,
        active_speaker_user_id=active_speaker_user_id,
        wake_probe=wake_probe,
        wake_detected=wake_detected,
        voice_like_prob=voice_like_prob,
        duration_sec=duration_sec,
        body_rms=body_rms,
        audio16k=audio16k,
        stt_sampling_rate=stt_sampling_rate,
        metrics=metrics,
        deps=build_voice_tts_interrupt_gate_deps(),
    )
    if interrupt_gate is None:
        return

    stt_execution = await run_voice_stt_execution_from_runtime(
        member=member,
        guild_id=guild_id,
        speaker_name=speaker_name,
        pcm_bytes=pcm_bytes,
        debug_meta=debug_meta,
        session_key=session_key,
        room_session_key=room_session_key,
        audio16k=audio16k,
        stt_sampling_rate=stt_sampling_rate,
        duration_sec=duration_sec,
        wake_probe=wake_probe,
        wake_detected=wake_detected,
        metrics=metrics,
        deps=build_voice_stt_execution_deps(),
    )
    if stt_execution is None:
        return

    text = stt_execution.text
    stt_meta = stt_execution.stt_meta
    partial_text = stt_execution.partial_text

    transcript_finalization = finalize_voice_transcript_from_runtime(
        member=member,
        text=text,
        partial_text=partial_text,
        session_key=session_key,
        room_session_key=room_session_key,
        turn_id=turn_id,
        wake_detected=wake_detected,
        wake_match_mode=wake_match_mode,
        wake_alias=wake_alias,
        wake_probe=wake_probe,
        wake_confirm=wake_confirm,
        wake_reject_reason=wake_reject_reason,
        duration_sec=duration_sec,
        metrics=metrics,
        deps=build_voice_transcript_finalize_deps(),
    )
    text = transcript_finalization.text
    transcript_result = transcript_finalization.transcript_result

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

is_control_command_authorized = make_control_command_authorized_checker(allowed_user_ids=ALLOWED_RESTART_USER_IDS)


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


def _mark_text_session_from_command(ctx, user_text: str, answer_text: str, *, awaiting_user_reply: bool = False) -> None:
    mark_text_session_from_command_runtime(
        ctx,
        user_text,
        answer_text,
        awaiting_user_reply=awaiting_user_reply,
        deps=build_discord_command_session_runtime_deps(),
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
