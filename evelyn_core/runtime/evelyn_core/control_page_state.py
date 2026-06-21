from __future__ import annotations

import time
import re
from typing import Any
from urllib.parse import quote

from control_page_runtime_health import (
    build_control_page_runtime_summary,
    is_control_plane_service_ready_state,
)

from .control_page_contracts import build_control_page_panel_state_payload
from .minecraft_runtime_snapshot import attach_minecraft_runtime_snapshot
from .text import clean_text


def command_status(value: bool) -> str:
    return "켜짐" if value else "꺼짐"


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


class ControlPageChatLogStore:
    def __init__(self, *, limit: int) -> None:
        self.limit = max(1, int(limit))
        self.rows_by_guild: dict[int, list[dict[str, Any]]] = {}

    def append(
        self,
        guild_id: int,
        role: str,
        author: str,
        text: str,
        *,
        now: float | None = None,
    ) -> None:
        cleaned_text = clean_text(text)
        if not cleaned_text:
            return
        cleaned_role = clean_text(role)
        rows = self.rows_by_guild.setdefault(int(guild_id), [])
        rows.append(
            {
                "role": cleaned_role,
                "author": clean_text(author) or ("Evelyn" if cleaned_role == "assistant" else "User"),
                "text": cleaned_text,
                "at": time.time() if now is None else float(now),
            }
        )
        if len(rows) > self.limit:
            del rows[:-self.limit]

    def get(self, guild_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows_by_guild.get(int(guild_id), [])]


class ControlPageUiCommandStore:
    def __init__(self, *, limit: int = 40) -> None:
        self.limit = max(1, int(limit))
        self.sequence = 0
        self.commands: list[dict[str, Any]] = []

    def enqueue(
        self,
        action: str,
        *,
        panel_id: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        self.sequence += 1
        command = {
            "id": self.sequence,
            "action": clean_text(action).lower(),
            "panel": panel_id,
            "at": time.time() if now is None else float(now),
        }
        self.commands.append(command)
        if len(self.commands) > self.limit:
            del self.commands[:-self.limit]
        return dict(command)

    def panel_state(self) -> dict[str, Any]:
        return build_control_page_panel_state_payload(
            self.commands,
            revision=self.sequence,
        )


class ControlPageMinecraftSnapshotCache:
    def __init__(self, *, stale_after_sec: float, expired_after_sec: float) -> None:
        self.stale_after_sec = max(0.0, float(stale_after_sec))
        self.expired_after_sec = max(0.0, float(expired_after_sec))
        self.snapshot: dict[str, Any] = {}
        self.cached_at = 0.0
        self.stale = True
        self.last_error = ""

    def has_snapshot(self) -> bool:
        return bool(self.snapshot)

    def age_seconds(self, *, now: float | None = None) -> float | None:
        if not self.cached_at:
            return None
        now_ts = time.time() if now is None else float(now)
        return max(0.0, now_ts - self.cached_at)

    def is_fresh(self, *, now: float | None = None) -> bool:
        age = self.age_seconds(now=now)
        return bool(self.snapshot and not self.stale and age is not None and age <= self.stale_after_sec)

    def snapshot_copy(self, *, now: float | None = None) -> dict[str, Any]:
        now_ts = time.time() if now is None else float(now)
        snapshot = dict(self.snapshot) if isinstance(self.snapshot, dict) else {}
        age = self.age_seconds(now=now_ts)
        snapshot["snapshot_age_sec"] = round(age, 3) if age is not None else None
        snapshot["snapshot_stale"] = bool(self.stale or (age is not None and age > self.stale_after_sec))
        snapshot["snapshot_expired"] = bool(
            snapshot["snapshot_stale"]
            and age is not None
            and age > self.expired_after_sec
        )
        if self.last_error and not snapshot.get("last_error"):
            snapshot["last_error"] = self.last_error
        snapshot = attach_minecraft_runtime_snapshot(
            snapshot,
            source="control_page_cache",
            now=now_ts,
            observed_at=self.cached_at or None,
            stale_after_sec=self.stale_after_sec,
            expired_after_sec=self.expired_after_sec,
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

    def store_success(self, snapshot: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        self.snapshot = dict(snapshot)
        self.cached_at = time.time() if now is None else float(now)
        self.stale = False
        self.last_error = clean_text(str(snapshot.get("last_error") or ""))
        return self.snapshot_copy(now=self.cached_at)

    def store_error(self, error_text: str, *, now: float | None = None) -> dict[str, Any]:
        _ = now
        self.last_error = clean_text(str(error_text or "")) or "minecraft_snapshot_error"
        self.stale = True
        if not self.snapshot:
            self.snapshot = {
                "last_error": self.last_error,
                "inventory_top": [],
                "inventory_summary": "inventory unavailable",
                "recent_activity": [],
            }
        else:
            self.snapshot["last_error"] = self.last_error
        return self.snapshot_copy()


class ControlPageRuntimeServicesCache:
    def __init__(
        self,
        *,
        stale_after_sec: float,
        expired_after_sec: float,
        refresh_min_interval_sec: float,
    ) -> None:
        self.stale_after_sec = max(0.0, float(stale_after_sec))
        self.expired_after_sec = max(0.0, float(expired_after_sec))
        self.refresh_min_interval_sec = max(0.0, float(refresh_min_interval_sec))
        self.services: dict[str, Any] = {}
        self.cached_at = 0.0
        self.last_refresh_request_at = 0.0

    def has_services(self) -> bool:
        return bool(self.services)

    def age_seconds(self, *, now: float | None = None) -> float | None:
        if not self.cached_at:
            return None
        now_ts = time.time() if now is None else float(now)
        return max(0.0, now_ts - self.cached_at)

    def snapshot_copy(self, *, refreshing: bool = False, now: float | None = None) -> dict[str, Any]:
        now_ts = time.time() if now is None else float(now)
        snapshot = dict(self.services) if isinstance(self.services, dict) else {}
        age = self.age_seconds(now=now_ts)
        snapshot["runtimeStatusAgeSec"] = round(age, 3) if age is not None else None
        snapshot["runtimeStatusRefreshing"] = bool(refreshing)
        snapshot["runtimeStatusStale"] = bool(age is not None and age > self.stale_after_sec)
        snapshot["runtimeStatusExpired"] = bool(age is not None and age > self.expired_after_sec)
        return snapshot

    def is_fresh(self, *, now: float | None = None) -> bool:
        age = self.age_seconds(now=now)
        return bool(self.services and age is not None and age <= self.stale_after_sec)

    def is_stale_not_expired(self, *, now: float | None = None) -> bool:
        age = self.age_seconds(now=now)
        return bool(
            self.services
            and age is not None
            and age > self.stale_after_sec
            and age <= self.expired_after_sec
        )

    def can_schedule_refresh(self, *, refreshing: bool = False, now: float | None = None) -> bool:
        if refreshing:
            return False
        now_ts = time.time() if now is None else float(now)
        if (now_ts - self.last_refresh_request_at) < self.refresh_min_interval_sec:
            return False
        return True

    def mark_refresh_request(self, *, now: float | None = None) -> None:
        self.last_refresh_request_at = time.time() if now is None else float(now)

    def store_success(self, services: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        self.services = dict(services)
        self.cached_at = time.time() if now is None else float(now)
        return self.snapshot_copy(now=self.cached_at)


def build_control_page_runtime_services_payload(
    *,
    service_results: dict[str, bool],
    voyager_ready: bool,
    voyager_error: str,
    bot_api_port_open: bool,
    bot_api_http_ready: bool,
    bot_api_state: str,
    bot_api_reason: str,
    bot_api_error: str,
    bot_api_error_kind: str,
    codex_required: bool,
    codex_ready: bool | None,
    codex_backend: str,
    codex_error: str,
) -> dict[str, Any]:
    cleaned_bot_api_state = clean_text(str(bot_api_state or "")) or "down"
    cleaned_bot_api_reason = clean_text(str(bot_api_reason or ""))
    bot_api_state_ready = is_control_plane_service_ready_state(cleaned_bot_api_state)
    bot_ready = bool(bot_api_http_ready and bot_api_state_ready)
    if bot_api_http_ready and not bot_api_state_ready and not cleaned_bot_api_reason:
        cleaned_bot_api_reason = "CP_BOT_STATE_NOT_READY"
    services = {
        "botReady": bot_ready,
        "mainReady": bool(service_results.get("main")),
        "routerReady": bool(service_results.get("router")),
        "subReady": bool(service_results.get("sub")),
        "ttsReady": bool(service_results.get("tts")),
        "voyagerReady": bool(voyager_ready),
        "codexRequired": bool(codex_required),
        "codexReady": codex_ready,
        "codexBackend": clean_text(str(codex_backend or "")) or "unknown",
        "botApiPortOpen": bool(bot_api_port_open),
        "botApiHttpReady": bool(bot_api_http_ready),
        "botApiState": cleaned_bot_api_state,
        "botApiReason": cleaned_bot_api_reason,
        "botApiError": clean_text(str(bot_api_error or "")),
        "botApiErrorKind": clean_text(str(bot_api_error_kind or "")),
        "summary": build_control_page_runtime_summary(
            bot_ready=bot_ready,
            voyager_ready=bool(voyager_ready),
            codex_required=bool(codex_required),
            codex_ready=codex_ready,
            bot_api_port_open=bool(bot_api_port_open),
            bot_api_http_ready=bool(bot_api_http_ready),
            bot_api_state=cleaned_bot_api_state,
            bot_api_reason_code=cleaned_bot_api_reason,
            bot_api_error=bot_api_error,
        ),
    }
    cleaned_voyager_error = clean_text(str(voyager_error or ""))
    cleaned_codex_error = clean_text(str(codex_error or ""))
    if cleaned_voyager_error:
        services["voyagerError"] = cleaned_voyager_error
    if cleaned_codex_error:
        services["codexError"] = cleaned_codex_error
    return services


def build_control_page_runtime_services_error_payload(
    error_text: str,
    *,
    action_backend: str,
) -> dict[str, Any]:
    cleaned_error = clean_text(str(error_text or "")) or "runtime_refresh_error"
    codex_backend = clean_text(str(action_backend or "")) or "unknown"
    codex_required = codex_backend.lower() == "codex-gateway"
    return {
        "botReady": False,
        "mainReady": False,
        "routerReady": False,
        "subReady": False,
        "ttsReady": False,
        "voyagerReady": False,
        "codexRequired": bool(codex_required),
        "codexReady": None,
        "codexBackend": codex_backend,
        "botApiPortOpen": False,
        "botApiHttpReady": False,
        "botApiState": "down",
        "botApiReason": "CP_RUNTIME_REFRESH_ERROR",
        "botApiError": cleaned_error,
        "botApiErrorKind": "runtime_refresh_error",
        "summary": build_control_page_runtime_summary(
            bot_ready=False,
            voyager_ready=False,
            codex_required=bool(codex_required),
            codex_ready=None,
            bot_api_port_open=False,
            bot_api_http_ready=False,
            bot_api_state="down",
            bot_api_reason_code="CP_RUNTIME_REFRESH_ERROR",
            bot_api_error=cleaned_error,
        ),
    }


def sanitize_control_page_welcome_text_payload(text: str, *, fallback: str) -> str:
    cleaned = clean_text(text).strip().strip("\"'“”‘’")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return clean_text(fallback)
    if len(cleaned) > 120:
        cleaned = cleaned[:120].rstrip(" ,.!?…") + "..."
    return cleaned


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


def build_control_page_runtime_diagnostics(
    runtime_services: dict[str, Any],
    *,
    control_api_ready: bool,
) -> dict[str, Any]:
    return {
        "controlApiReady": bool(control_api_ready),
        "botApiPortOpen": bool(runtime_services.get("botApiPortOpen")),
        "botApiState": clean_text(str(runtime_services.get("botApiState") or "unknown")),
        "botApiReason": clean_text(str(runtime_services.get("botApiReason") or "")),
        "botApiError": clean_text(str(runtime_services.get("botApiError") or "")),
    }


def build_control_page_boot_progress_payload(
    runtime_services: dict[str, Any] | None,
    *,
    startup_steps: tuple[tuple[str, str], ...],
    startup_component_state: dict[str, dict[str, Any]],
    startup_components_ready: bool,
    discord_enabled: bool,
    discord_ready: bool,
    guild_available: bool,
    control_api_available: bool,
    listening: bool = False,
) -> dict[str, Any]:
    services = runtime_services or {}
    main_ready = bool(services.get("mainReady"))
    tts_ready = bool(services.get("ttsReady"))
    if "botApiHttpReady" in services:
        control_api_ready = bool(services.get("botApiHttpReady"))
    else:
        control_api_ready = bool(control_api_available)
    control_api_detail = clean_text(
        str(services.get("botApiReason") or services.get("botApiState") or services.get("botApiError") or "")
    )
    service_done = {
        "main_service": main_ready,
        "router_service": bool(services.get("routerReady")),
        "sub_service": bool(services.get("subReady")),
        "tts_service": tts_ready,
        "discord_gateway": bool((not discord_enabled) or (discord_ready and guild_available)),
        "control_api": bool(control_api_ready),
        "opus": (startup_component_state.get("opus") or {}).get("status") == "done",
        "stt": (startup_component_state.get("stt") or {}).get("status") == "done",
        "main_warmup": (startup_component_state.get("main_warmup") or {}).get("status") == "done" or main_ready,
        "tts_warmup": (startup_component_state.get("tts_warmup") or {}).get("status") == "done" or tts_ready,
    }
    if listening:
        service_done["voice_listening"] = True

    steps: list[dict[str, Any]] = []
    for key, label in startup_steps:
        component = startup_component_state.get(key) or {}
        done = bool(service_done.get(key))
        status = "done" if done else clean_text(str(component.get("status") or "pending"))
        detail = clean_text(str(component.get("detail") or ""))
        if key == "control_api" and not done:
            status = "down" if control_api_detail else status
            if not detail and control_api_detail:
                detail = control_api_detail
        steps.append(
            {
                "key": key,
                "label": label,
                "done": done,
                "status": status,
                "detail": detail,
                "updatedAt": component.get("updatedAt"),
            }
        )
    done_count = sum(1 for step in steps if step["done"])
    percent = round((done_count / max(1, len(steps))) * 100)
    current = next((step for step in steps if not step["done"]), steps[-1])
    phase = "전체 서버 준비 완료" if percent >= 100 else f"{current['label']} 준비 중"
    return {
        "percent": percent,
        "phase": phase,
        "source": "bot_processor",
        "ready": percent >= 100,
        "componentsReady": bool(startup_components_ready),
        "steps": steps,
    }


def build_control_page_voice_payload(
    *,
    channel_name: str,
    listening: bool,
    speaking: bool,
    tts_target_name: str,
) -> dict[str, Any]:
    return {
        "channelName": clean_text(channel_name) or "없음",
        "listening": bool(listening),
        "speaking": bool(speaking),
        "ttsTargetName": clean_text(tts_target_name) or "없음",
    }


def build_control_page_runtime_payload(
    *,
    main_model: str,
    router_model: str,
    summary_model: str,
    stt_model: str,
    inflight_llm_requests: int,
    tts_backlog: int,
    output_mode: str,
    local_tts_output: dict[str, Any],
    model_call_metrics: dict[str, Any],
    question_metrics: dict[str, Any],
    local_mic: dict[str, Any],
    voice_pipeline: dict[str, Any],
    vision_watch: dict[str, Any],
    services: dict[str, Any],
    diagnostics: dict[str, Any],
    service_health: dict[str, Any],
    control_page_panels: dict[str, Any],
    boot_progress: dict[str, Any],
    voice_debug_audio: bool | None = None,
    local_mic_target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "mainModel": clean_text(main_model),
        "routerModel": clean_text(router_model),
        "summaryModel": clean_text(summary_model),
        "sttModel": clean_text(stt_model),
        "inflightLlmRequests": int(inflight_llm_requests),
        "ttsBacklog": int(tts_backlog),
        "outputMode": clean_text(output_mode) or "none",
        "localTtsOutput": dict(local_tts_output or {}),
        "modelCallMetrics": dict(model_call_metrics or {}),
        "questionMetrics": dict(question_metrics or {}),
        "localMic": dict(local_mic or {}),
        "voicePipeline": dict(voice_pipeline or {}),
        "visionWatch": dict(vision_watch or {}),
        "services": dict(services or {}),
        "diagnostics": dict(diagnostics or {}),
        "serviceHealth": dict(service_health or {}),
        "controlPagePanels": dict(control_page_panels or {}),
        "bootProgress": dict(boot_progress or {}),
    }
    if voice_debug_audio is not None:
        payload["voiceDebugAudio"] = bool(voice_debug_audio)
    if local_mic_target is not None:
        payload["localMicTarget"] = dict(local_mic_target or {})
    return payload


def build_control_page_minecraft_payload(
    minecraft: dict[str, Any],
    *,
    minecraft_session_active: bool,
    minecraft_status_fields: dict[str, Any],
) -> dict[str, Any]:
    activity = minecraft.get("recent_activity") if isinstance(minecraft.get("recent_activity"), list) else []
    idle_summary = (
        "Voyager는 켜져 있지만 아직 Minecraft 플레이 상태는 아니야. 접속이 잡히면 위젯이 자동으로 나타나."
        if minecraft.get("minecraft_autonomy")
        else "지금은 Minecraft 플레이 전이야. /minecraft connect 를 실행하면 플레이 상태 위젯이 자동으로 나타나."
    )
    return {
        "running": bool(minecraft.get("minecraft_autonomy")),
        "connected": bool(minecraft.get("voyager_connected")),
        "sessionActive": bool(minecraft_session_active),
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
        **dict(minecraft_status_fields or {}),
        "idleSummary": "" if minecraft_session_active else idle_summary,
    }


def build_control_page_local_state_payload(
    *,
    ok: bool,
    generated_at: float,
    local_url: str,
    boot_progress: dict[str, Any],
    ui_state: dict[str, Any],
    guild_id: int,
    guild_name: str,
    commands: list[dict[str, Any]],
    all_commands: list[dict[str, Any]],
    chat_messages: list[dict[str, Any]],
    control_page_panels: dict[str, Any],
    voice: dict[str, Any],
    runtime: dict[str, Any],
    status_text: str,
) -> dict[str, Any]:
    return {
        "ok": bool(ok),
        "generatedAt": float(generated_at),
        "localUrl": clean_text(local_url),
        "bootProgress": dict(boot_progress or {}),
        "ui": dict(ui_state or {}),
        "guild": {"id": int(guild_id), "name": clean_text(guild_name)} if ok else None,
        "commands": list(commands or []),
        "allCommands": list(all_commands or []),
        "chat": {"messages": list(chat_messages or []) if ok else []},
        "controlPagePanels": dict(control_page_panels or {}),
        "voice": dict(voice or {}),
        "runtime": dict(runtime or {}),
        "minecraft": {},
        "statusText": clean_text(status_text),
    }


def build_control_page_guild_state_payload(
    *,
    generated_at: float,
    local_url: str,
    boot_progress: dict[str, Any],
    ui_state: dict[str, Any],
    guild_id: int,
    guild_name: str,
    commands: list[dict[str, Any]],
    all_commands: list[dict[str, Any]],
    chat_messages: list[dict[str, Any]],
    control_page_panels: dict[str, Any],
    voice: dict[str, Any],
    runtime: dict[str, Any],
    minecraft: dict[str, Any],
    status_text: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "generatedAt": float(generated_at),
        "localUrl": clean_text(local_url),
        "bootProgress": dict(boot_progress or {}),
        "ui": dict(ui_state or {}),
        "guild": {"id": int(guild_id), "name": clean_text(guild_name)},
        "commands": list(commands or []),
        "allCommands": list(all_commands or []),
        "chat": {"messages": list(chat_messages or [])},
        "controlPagePanels": dict(control_page_panels or {}),
        "voice": dict(voice or {}),
        "runtime": dict(runtime or {}),
        "minecraft": dict(minecraft or {}),
        "statusText": clean_text(status_text),
    }


def build_control_page_local_state_view(
    *,
    generated_at: float,
    local_url: str,
    local_mode: bool,
    local_guild_id: int,
    local_guild_name: str,
    commands: list[dict[str, Any]],
    all_commands: list[dict[str, Any]],
    chat_messages: list[dict[str, Any]],
    panel_state: dict[str, Any],
    runtime_services: dict[str, Any],
    runtime_diagnostics: dict[str, Any],
    runtime_health: dict[str, Any],
    boot_progress: dict[str, Any],
    local_tts: dict[str, Any],
    local_mic: dict[str, Any],
    local_listening: bool,
    voice_pipeline: dict[str, Any],
    vision_watch: dict[str, Any],
    main_model: str,
    router_model: str,
    summary_model: str,
    stt_model: str,
    inflight_llm_requests: int,
    tracked_tts_count: int,
    output_mode: str,
    model_call_metrics: dict[str, Any],
    question_metrics: dict[str, Any],
    status_text: str,
) -> dict[str, Any]:
    local_speaking = bool(local_tts.get("active"))
    ui_state = build_control_page_ui_state(
        guild_available=local_mode,
        listening=local_listening,
        speaking=False,
        minecraft_running=False,
        minecraft_session_active=False,
        minecraft_snapshot_stale=False,
        minecraft_last_error="",
    )
    voice_payload = build_control_page_voice_payload(
        channel_name="없음",
        listening=local_listening,
        speaking=local_speaking,
        tts_target_name="로컬 스피커" if local_speaking else "없음",
    )
    runtime_payload = build_control_page_runtime_payload(
        main_model=main_model,
        router_model=router_model,
        summary_model=summary_model,
        stt_model=stt_model,
        inflight_llm_requests=inflight_llm_requests,
        tts_backlog=int(tracked_tts_count) + (1 if local_speaking else 0),
        output_mode=output_mode,
        local_tts_output=local_tts,
        model_call_metrics=model_call_metrics,
        question_metrics=question_metrics,
        local_mic=local_mic,
        voice_pipeline=voice_pipeline,
        vision_watch=vision_watch,
        services=runtime_services,
        diagnostics=runtime_diagnostics,
        service_health=runtime_health,
        control_page_panels=panel_state,
        boot_progress=boot_progress,
    )
    return build_control_page_local_state_payload(
        ok=local_mode,
        generated_at=generated_at,
        local_url=local_url,
        boot_progress=boot_progress,
        ui_state=ui_state,
        guild_id=local_guild_id,
        guild_name=local_guild_name,
        commands=commands,
        all_commands=all_commands,
        chat_messages=chat_messages,
        control_page_panels=panel_state,
        voice=voice_payload,
        runtime=runtime_payload,
        status_text=status_text,
    )


def build_control_page_guild_state_view(
    *,
    generated_at: float,
    local_url: str,
    guild_id: int,
    guild_name: str,
    voice_channel_name: str,
    listening: bool,
    speaking: bool,
    tts_target_name: str,
    commands: list[dict[str, Any]],
    all_commands: list[dict[str, Any]],
    chat_messages: list[dict[str, Any]],
    panel_state: dict[str, Any],
    runtime_services: dict[str, Any],
    runtime_diagnostics: dict[str, Any],
    runtime_health: dict[str, Any],
    boot_progress: dict[str, Any],
    local_tts: dict[str, Any],
    local_mic: dict[str, Any],
    voice_pipeline: dict[str, Any],
    vision_watch: dict[str, Any],
    main_model: str,
    router_model: str,
    summary_model: str,
    stt_model: str,
    inflight_llm_requests: int,
    tracked_tts_count: int,
    output_mode: str,
    model_call_metrics: dict[str, Any],
    question_metrics: dict[str, Any],
    minecraft: dict[str, Any],
    minecraft_session_active: bool,
    minecraft_status_fields: dict[str, Any],
    voice_debug_audio: bool,
    local_mic_target: dict[str, Any],
    status_text: str,
) -> dict[str, Any]:
    ui_state = build_control_page_ui_state(
        guild_available=True,
        listening=listening,
        speaking=speaking,
        minecraft_running=bool(minecraft.get("minecraft_autonomy")),
        minecraft_session_active=minecraft_session_active,
        minecraft_snapshot_stale=bool(minecraft.get("snapshot_stale")),
        minecraft_last_error=minecraft.get("last_error"),
    )
    voice_payload = build_control_page_voice_payload(
        channel_name=voice_channel_name,
        listening=listening,
        speaking=speaking,
        tts_target_name=tts_target_name,
    )
    runtime_payload = build_control_page_runtime_payload(
        main_model=main_model,
        router_model=router_model,
        summary_model=summary_model,
        stt_model=stt_model,
        inflight_llm_requests=inflight_llm_requests,
        tts_backlog=int(tracked_tts_count) + (1 if local_tts.get("active") else 0),
        output_mode=output_mode,
        local_tts_output=local_tts,
        model_call_metrics=model_call_metrics,
        question_metrics=question_metrics,
        local_mic=local_mic,
        voice_pipeline=voice_pipeline,
        vision_watch=vision_watch,
        services=runtime_services,
        diagnostics=runtime_diagnostics,
        service_health=runtime_health,
        control_page_panels=panel_state,
        boot_progress=boot_progress,
        voice_debug_audio=voice_debug_audio,
        local_mic_target=local_mic_target,
    )
    minecraft_payload = build_control_page_minecraft_payload(
        minecraft,
        minecraft_session_active=minecraft_session_active,
        minecraft_status_fields=minecraft_status_fields,
    )
    return build_control_page_guild_state_payload(
        generated_at=generated_at,
        local_url=local_url,
        boot_progress=boot_progress,
        ui_state=ui_state,
        guild_id=guild_id,
        guild_name=guild_name,
        commands=commands,
        all_commands=all_commands,
        chat_messages=chat_messages,
        control_page_panels=panel_state,
        voice=voice_payload,
        runtime=runtime_payload,
        minecraft=minecraft_payload,
        status_text=status_text,
    )


def control_page_chat_refresh_plan(text: str) -> dict[str, Any]:
    normalized = clean_text(text).lower()
    needs_fresh_snapshot = (
        normalized.startswith("/minecraft")
        or normalized in {"/inventory", "/voyager stats", "/minecraft status", "/mc-status"}
    )
    return {
        "normalized": normalized,
        "needs_fresh_snapshot": bool(needs_fresh_snapshot),
        "needs_runtime_refresh": bool(normalized.startswith("/minecraft")),
    }


def parse_control_page_guild_id(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def control_page_query_flag(value: Any) -> bool:
    return clean_text(str(value or "")).lower() in {"1", "true", "yes", "on"}


def parse_control_page_memory_graph_query(query: Any) -> dict[str, Any]:
    try:
        max_nodes = int(query.get("max_nodes", "160"))
    except Exception:
        max_nodes = 160
    return {
        "max_nodes": max(1, max_nodes),
        "include_internal": control_page_query_flag(query.get("include_internal")),
    }


def parse_control_page_memory_snapshot_query(query: Any) -> dict[str, Any]:
    try:
        limit = int(query.get("limit", "80"))
    except Exception:
        limit = 80
    return {
        "include_hidden": control_page_query_flag(query.get("include_hidden")),
        "include_internal": control_page_query_flag(query.get("include_internal")),
        "limit": max(1, limit),
    }


def parse_control_page_memory_note_query(query: Any) -> dict[str, Any]:
    return {
        "include_internal": control_page_query_flag(query.get("include_internal")),
    }


def parse_control_page_memory_note_action_payload(payload: Any) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    return {
        "action": clean_text(str(body.get("action") or "")),
        "title": body.get("title"),
        "body": body.get("body"),
    }


def control_page_result_status(result: dict[str, Any], *, ok_status: int = 200, error_status: int = 404) -> int:
    return int(ok_status if result.get("ok") else error_status)


def handle_control_page_memory_note_action_request(
    note_id: str,
    payload: Any,
    *,
    update_note: Any,
) -> tuple[dict[str, Any], int]:
    note_action = parse_control_page_memory_note_action_payload(payload)
    result = update_note(
        note_id,
        note_action.get("action"),
        title=note_action.get("title"),
        body=note_action.get("body"),
    )
    return result, control_page_result_status(result)


def parse_control_page_chat_payload(payload: Any) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    text = clean_text(str(body.get("text") or ""))
    if not text:
        return {
            "ok": False,
            "error": "empty_text",
            "status": 400,
            "text": "",
            "guild_id": None,
        }
    return {
        "ok": True,
        "error": "",
        "status": 200,
        "text": text,
        "guild_id": parse_control_page_guild_id(body.get("guildId")),
    }


async def handle_control_page_chat_request(
    payload: Any,
    *,
    discord_enabled: bool,
    select_guild: Any,
    effective_guild_id: Any,
    append_chat_log: Any,
    handle_input: Any,
    ensure_minecraft_snapshot: Any,
    refresh_runtime_services: Any,
    build_state: Any,
    user_author: str = "정훈",
    assistant_author: str = "Evelyn",
) -> tuple[dict[str, Any], int]:
    chat_request = parse_control_page_chat_payload(payload)
    if not chat_request.get("ok"):
        return (
            {"ok": False, "error": chat_request.get("error") or "invalid_request"},
            int(chat_request.get("status") or 400),
        )
    text = str(chat_request.get("text") or "")
    guild = select_guild(chat_request.get("guild_id"))
    if guild is None and discord_enabled:
        return {"ok": False, "error": "guild_not_available"}, 503

    guild_id = int(effective_guild_id(guild))
    append_chat_log(guild_id, "user", user_author, text)
    try:
        reply_text = await handle_input(guild, text)
    except Exception as exc:
        reply_text = f"처리 중 오류가 났어: {exc}"
    append_chat_log(guild_id, "assistant", assistant_author, reply_text)

    refresh_plan = control_page_chat_refresh_plan(text)
    needs_fresh_snapshot = bool(refresh_plan.get("needs_fresh_snapshot"))
    if guild is not None:
        await ensure_minecraft_snapshot(
            getattr(guild, "id", None),
            force=needs_fresh_snapshot,
            wait=needs_fresh_snapshot,
        )
    if refresh_plan.get("needs_runtime_refresh"):
        await refresh_runtime_services(force=True)
    state = await build_state(guild)
    return {"ok": True, "reply": reply_text, "state": state}, 200


async def handle_control_page_shutdown_request(
    guild_id_value: Any,
    *,
    select_guild: Any,
    handle_input: Any,
    build_state: Any,
) -> tuple[dict[str, Any], int]:
    guild = select_guild(parse_control_page_guild_id(guild_id_value))
    reply_text = await handle_input(guild, "/shutdown")
    state = await build_state(guild)
    return {"ok": True, "reply": reply_text, "state": state}, 200


def memory_vault_obsidian_url(vault_path: Any) -> str:
    return "obsidian://open?path=" + quote(str(vault_path), safe="")


def memory_vault_open_tool_reply(*, outcome: str, error: Any = "") -> str:
    if outcome == "obsidian":
        return "Obsidian 메모리 vault를 열게."
    if outcome == "folder":
        return f"Obsidian protocol이 실패해서 vault 폴더를 대신 열었어: {error}"
    return f"Obsidian 메모리 vault를 열지 못했어: {error}"


def control_page_open_memory_vault_tool_reply(
    *,
    vault_path: Any,
    obsidian_url: str,
    open_url: Any,
    open_path: Any,
) -> str:
    try:
        open_url(obsidian_url)
        return memory_vault_open_tool_reply(outcome="obsidian")
    except Exception as exc:
        try:
            open_path(vault_path)
            return memory_vault_open_tool_reply(outcome="folder", error=exc)
        except Exception as fallback_exc:
            return memory_vault_open_tool_reply(outcome="failed", error=fallback_exc)


def control_page_open_memory_vault_payload(
    *,
    vault_path: Any,
    obsidian_url: str,
    outcome: str,
    error: Any = "",
) -> dict[str, Any]:
    if outcome == "obsidian":
        return {
            "ok": True,
            "message": "Obsidian memory vault open request sent.",
            "vaultPath": str(vault_path),
            "url": obsidian_url,
        }
    if outcome == "folder":
        return {
            "ok": True,
            "message": f"Obsidian protocol failed, opened the vault folder instead: {error}",
            "vaultPath": str(vault_path),
            "url": obsidian_url,
            "fallback": "folder",
        }
    return {
        "ok": False,
        "error": "open_memory_vault_failed",
        "message": str(error),
        "vaultPath": str(vault_path),
        "url": obsidian_url,
    }


def control_page_open_memory_vault_result(
    *,
    vault_path: Any,
    obsidian_url: str,
    open_url: Any,
    open_path: Any,
) -> tuple[dict[str, Any], int]:
    try:
        open_url(obsidian_url)
        return (
            control_page_open_memory_vault_payload(
                vault_path=vault_path,
                obsidian_url=obsidian_url,
                outcome="obsidian",
            ),
            200,
        )
    except Exception as exc:
        try:
            open_path(vault_path)
            return (
                control_page_open_memory_vault_payload(
                    vault_path=vault_path,
                    obsidian_url=obsidian_url,
                    outcome="folder",
                    error=exc,
                ),
                200,
            )
        except Exception as fallback_exc:
            return (
                control_page_open_memory_vault_payload(
                    vault_path=vault_path,
                    obsidian_url=obsidian_url,
                    outcome="failed",
                    error=fallback_exc,
                ),
                500,
            )


def build_control_page_status_text_payload(
    *,
    guild_name: str,
    voice_channel_name: str,
    listening: bool,
    speaking: bool,
    tts_target: str,
    voice_input_mode: str,
    local_mic_status: str,
    main_model: str,
    router_model: str,
    summary_model: str,
    stt_model: str,
    minecraft: dict[str, Any],
) -> str:
    runtime_snapshot = minecraft.get("runtime_snapshot") if isinstance(minecraft.get("runtime_snapshot"), dict) else {}
    snapshot_freshness = clean_text(str(runtime_snapshot.get("freshness") or minecraft.get("snapshot_freshness") or "unknown"))
    snapshot_age = runtime_snapshot.get("age_sec", minecraft.get("snapshot_age_sec"))
    return "\n".join(
        [
            "Evelyn 상태",
            f"- 서버: {clean_text(guild_name)}",
            f"- 음성 채널: {clean_text(voice_channel_name) or '없음'}",
            f"- 듣기: {command_status(listening)}",
            f"- TTS 발화: {command_status(speaking)}",
            f"- 발화 대상: {clean_text(tts_target) or '없음'}",
            f"- 입력 모드: {clean_text(voice_input_mode)}",
            f"- 로컬 마이크: {clean_text(local_mic_status)}",
            "",
            "모델",
            f"- Main: {clean_text(main_model)}",
            f"- Router: {clean_text(router_model)}",
            f"- Summary: {clean_text(summary_model)}",
            f"- STT: {clean_text(stt_model)}",
            "",
            "Minecraft",
            f"- Voyager 실행: {command_status(bool(minecraft.get('minecraft_autonomy')))}",
            f"- 연결: {command_status(bool(minecraft.get('voyager_connected')))}",
            f"- 스냅샷: {snapshot_freshness} ({snapshot_age if snapshot_age is not None else 'unknown'}s)",
            f"- 현재 task: {minecraft.get('current_task') or '없음'}",
            f"- 목표: {minecraft.get('goal') or '없음'}",
        ]
    )


def build_control_page_local_status_text_payload(
    runtime_services: dict[str, Any] | None,
    *,
    discord_enabled: bool,
    local_url: str,
    bot_api_host: str,
    bot_api_port: int,
    main_model: str,
    router_model: str,
    summary_model: str,
    stt_model: str,
    local_speaking: bool,
    local_listening: bool,
    local_mic_status: str,
) -> str:
    services = dict(runtime_services or {})
    bot_api_state = clean_text(str(services.get("botApiState") or ""))
    codex_required = bool(services.get("codexRequired"))
    codex_ready = bool(services.get("codexReady")) if services.get("codexReady") is not None else False
    lines = [
        "Evelyn 로컬 상태",
        f"- 모드: local-only ({'Discord 꺼짐' if not discord_enabled else 'Discord 켜짐'})",
        f"- Bot API: {clean_text(local_url)}",
        f"- Bot API 상태: {command_status(bool(services.get('botApiHttpReady')))} ({bot_api_state or 'unknown'})",
        f"- Bot API 포트: {clean_text(bot_api_host)}:{int(bot_api_port)} {command_status(bool(services.get('botApiPortOpen')))}",
        f"- Main LLM: {command_status(bool(services.get('mainReady')))} / {clean_text(main_model)}",
        f"- Router LLM: {command_status(bool(services.get('routerReady')))} / {clean_text(router_model)}",
        f"- Summary LLM: {command_status(bool(services.get('subReady')))} / {clean_text(summary_model)}",
        f"- TTS: {command_status(bool(services.get('ttsReady')))}",
        f"- STT: {clean_text(stt_model)}",
        f"- 로컬 스피커: {command_status(local_speaking)}",
        f"- 로컬 마이크: {clean_text(local_mic_status)}",
        f"- 로컬 듣기: {command_status(local_listening)}",
        f"- Minecraft/Voyager: {command_status(bool(services.get('voyagerReady')))}",
    ]
    if codex_required:
        lines.append(f"- Codex gateway: {command_status(codex_ready)}")
    summary = clean_text(str(services.get("summary") or ""))
    if summary:
        lines.append(f"- 요약: {summary}")
    bot_api_error = clean_text(str(services.get("botApiError") or ""))
    if bot_api_error:
        lines.append(f"- Bot API 에러: {bot_api_error}")
    codex_error = clean_text(str(services.get("codexError") or ""))
    if codex_error:
        lines.append(f"- Codex 상태: {codex_error}")
    return "\n".join(lines)


def build_control_page_voice_status_reply_payload(
    voice: dict[str, Any],
    *,
    channel_name: str,
    voice_input_mode: str,
    local_mic_status: str,
    continuity_detail_lines: list[str],
) -> str:
    saved = voice.get("lastVoiceChannel") if isinstance(voice.get("lastVoiceChannel"), dict) else {}
    saved_channel = clean_text(str((saved or {}).get("channel_name") or "")) or "none"
    local_tts = voice.get("localTtsOutput") if isinstance(voice.get("localTtsOutput"), dict) else {}
    lines = [
        "음성 상태",
        f"- 현재 채널: {clean_text(channel_name) or 'none'}",
        f"- 저장된 채널: {saved_channel}",
        f"- 출력 모드: {voice.get('outputMode') or 'unknown'}",
        f"- 로컬 출력: enabled={local_tts.get('enabled')} active={local_tts.get('active')} device={local_tts.get('device') or 'default'}",
        f"- 입력 모드: {clean_text(voice_input_mode)}",
        f"- 로컬 마이크: {clean_text(local_mic_status)}",
        "",
        "파이프라인",
        f"- 큐: {voice['queueDepth']}/{voice['queueMax']}",
        f"- 최근 음성: {'있음' if voice['liveRecent'] else '없음'}",
        f"- STT 처리 중: {command_status(bool(voice['sttBusy']))}",
        f"- STT 쿨다운: {voice['sttCooldownRemainingSec']}s",
        f"- STT timeout: {voice['sttTimeoutCount']}",
        f"- 큐 drop: full={voice['queueFullDropCount']} stale={voice['queueStaleDropCount']}",
        f"- 실패: llm={voice['llmFailedCount']} tts_req={voice['ttsRequestFailedCount']} playback={voice['ttsPlaybackFailedCount']} delivery={voice['voiceDeliveryFailedCount']}",
        f"- p95: stt={voice['sttMsP95']}ms tts_first={voice['ttsFirstAudioMsP95']}ms main_first={voice['mainFirstTokenMsP95']}ms",
        "- 바리인 연속성",
    ]
    lines.extend(list(continuity_detail_lines or []))
    return "\n".join(lines)


def build_control_page_voice_continuity_reply_payload(continuity_detail_lines: list[str]) -> str:
    return "\n".join(["바리인 연속성", *list(continuity_detail_lines or [])])


def build_control_page_voice_input_mode_reply(*, voice_input_mode: str, mode: str) -> str:
    return f"음성 입력 모드: {clean_text(voice_input_mode)} ({clean_text(mode)})"


def build_control_page_voice_reconnect_reply(*, ok: bool, detail: str) -> str:
    if ok:
        return f"음성 채널에 다시 연결했어: {clean_text(detail)}"
    return f"음성 재연결 실패: {clean_text(detail)}"


def control_page_discord_required_reply() -> str:
    return "그 명령은 Discord 연결이 필요해."


def build_control_page_voice_continuity_reset_required_reply() -> str:
    return "바리인 연속성 리셋은 확인이 필요해. `/voice continuity reset confirm`로 실행해줘."


def build_control_page_voice_continuity_reset_reply(continuity_reply: str) -> str:
    return "바리인 연속성 카운터를 리셋했어.\n" + str(continuity_reply or "").strip()


def build_control_page_inventory_reply_payload(minecraft: dict[str, Any]) -> str:
    entries = minecraft.get("inventory_top") if isinstance(minecraft.get("inventory_top"), list) else []
    if not entries:
        return "현재 인벤토리 정보를 아직 받지 못했어."
    lines = ["Minecraft 인벤토리 요약"]
    for row in entries:
        if isinstance(row, dict):
            lines.append(f"- {row['name']}: {row['count']}")
    return "\n".join(lines)


def build_control_page_minecraft_reply_payload(minecraft: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Minecraft 상태",
            f"- Voyager 실행: {command_status(bool(minecraft.get('minecraft_autonomy')))}",
            f"- 연결: {command_status(bool(minecraft.get('voyager_connected')))}",
            f"- 목표: {minecraft.get('goal') or '없음'}",
            f"- stage: {minecraft.get('stage') or '없음'}",
            f"- task: {minecraft.get('current_task') or '없음'}",
            f"- task_stage: {minecraft.get('current_task_stage') or '없음'}",
            f"- 진행: {minecraft.get('progress') or '없음'}",
            "",
            "Voyager 지표",
            f"- tech_tree: {clean_text(str(minecraft.get('voyager_tech_tree_highest') or 'unknown'))}",
            f"- unique_items: {minecraft.get('voyager_unique_item_count') if minecraft.get('voyager_unique_item_count') is not None else 'unknown'}",
            f"- skill_library: {minecraft.get('voyager_skill_library_size') if minecraft.get('voyager_skill_library_size') is not None else 'unknown'}",
            f"- travel_distance: {minecraft.get('voyager_travel_distance_blocks') if minecraft.get('voyager_travel_distance_blocks') is not None else 'unknown'}",
            "",
            "현재 상태",
            f"- health: {minecraft.get('health') if minecraft.get('health') is not None else 'unknown'}",
            f"- hunger: {minecraft.get('hunger') if minecraft.get('hunger') is not None else 'unknown'}",
            f"- position: {minecraft.get('position_text') or 'unknown'}",
        ]
    )


def build_control_page_shutdown_reply(*, local_mode: bool, helper_started: bool) -> str:
    if local_mode:
        if helper_started:
            return "Local Evelyn shutdown started. Only Evelyn local runtime windows and ports will be stopped."
        return "Local shutdown helper failed, so only the local Evelyn process is stopping."
    if helper_started:
        return "Evelyn runtime 종료를 시작했어. supervisors, bot, LLM, TTS, Voyager, Evelyn-owned WSL services를 정리해."
    return "종료 helper 실행에 실패해서 bot process만 정리할게."


def build_control_page_shutdown_tool_reply(
    *,
    guild_available: bool,
    schedule_local_shutdown: Any,
    schedule_stack_shutdown: Any,
    schedule_bot_shutdown: Any,
) -> str:
    if not guild_available:
        if schedule_local_shutdown():
            return build_control_page_shutdown_reply(local_mode=True, helper_started=True)
        schedule_bot_shutdown()
        return build_control_page_shutdown_reply(local_mode=True, helper_started=False)
    if schedule_stack_shutdown():
        return build_control_page_shutdown_reply(local_mode=False, helper_started=True)
    schedule_bot_shutdown()
    return build_control_page_shutdown_reply(local_mode=False, helper_started=False)


def build_control_page_minecraft_connect_reply_payload(observed: dict[str, Any], *, position_text: str) -> str:
    goal = clean_text(str(observed.get("objective_goal") or observed.get("goal") or "없음")) or "없음"
    stage = clean_text(str(observed.get("objective_stage") or observed.get("stage") or "없음")) or "없음"
    return "\n".join(
        [
            "Voyager Minecraft 모드를 시작했어.",
            f"- goal: {goal}",
            f"- stage: {stage}",
            f"- position: {clean_text(position_text)}",
        ]
    )


def build_control_page_minecraft_disconnect_reply() -> str:
    return "Voyager Minecraft 모드를 중지했어."


def build_control_page_minecraft_goal_missing_reply() -> str:
    return "목표를 같이 적어줘. 예: /minecraft goal progress_to_diamond"


def build_control_page_minecraft_goal_updated_reply(goal_text: str, status: dict[str, Any]) -> str:
    stage = clean_text(str(status.get("stage") or "unknown")) or "unknown"
    return "\n".join(
        [
            "Minecraft 목표를 바꿨어.",
            f"- goal: {clean_text(goal_text)}",
            f"- stage: {stage}",
        ]
    )


async def execute_control_page_memory_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    execute_memory_panel_action: Any,
    enqueue_ui_command: Any,
    ensure_vault_layout: Any,
    open_vault_tool_reply: Any,
    vault_obsidian_url: Any,
    open_url: Any,
    open_path: Any,
) -> str | None:
    if tool_name == "control_page.memory_panel":
        return execute_memory_panel_action(clean_text(str(arguments.get("action") or "toggle")))
    if tool_name == "memory.open_vault":
        enqueue_ui_command("toggle", panel_id="memory")
        vault = ensure_vault_layout()
        return open_vault_tool_reply(
            vault_path=vault,
            obsidian_url=vault_obsidian_url(vault),
            open_url=open_url,
            open_path=open_path,
        )
    return None


async def execute_control_page_runtime_tool(
    tool_name: str,
    *,
    guild: Any,
    get_runtime_services: Any,
    build_local_status_text: Any,
    build_status_reply: Any,
    execute_restart_command: Any,
    schedule_local_shutdown: Any,
    schedule_stack_shutdown: Any,
    schedule_bot_shutdown: Any,
    build_autonomy_reply: Any,
) -> str | None:
    if tool_name == "runtime.status":
        if guild is None:
            services = await get_runtime_services(force=True)
            return build_local_status_text(services)
        return await build_status_reply(guild)
    if tool_name == "runtime.restart_bot":
        return execute_restart_command()
    if tool_name == "runtime.shutdown_stack":
        return build_control_page_shutdown_tool_reply(
            guild_available=guild is not None,
            schedule_local_shutdown=schedule_local_shutdown,
            schedule_stack_shutdown=schedule_stack_shutdown,
            schedule_bot_shutdown=schedule_bot_shutdown,
        )
    if tool_name == "autonomy.status":
        if guild is None:
            return control_page_discord_required_reply()
        return build_autonomy_reply(guild)
    return None


async def execute_control_page_voice_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    guild: Any,
    build_voice_status_reply: Any,
    set_input_mode: Any,
    input_mode_status_line: Any,
    restore_voice_channel: Any,
    build_voice_continuity_reply: Any,
    reset_continuity_probe: Any,
) -> str | None:
    if tool_name == "voice.status":
        return build_voice_status_reply(guild)
    if tool_name == "voice.input_mode":
        requested = clean_text(str(arguments.get("mode") or "auto"))
        mode = set_input_mode(requested)
        return build_control_page_voice_input_mode_reply(
            voice_input_mode=input_mode_status_line(),
            mode=mode,
        )
    if tool_name == "voice.reconnect":
        if guild is None:
            return control_page_discord_required_reply()
        ok, detail = await restore_voice_channel(guild, force=True)
        return build_control_page_voice_reconnect_reply(ok=ok, detail=detail)
    if tool_name == "voice.continuity":
        return build_voice_continuity_reply(guild)
    if tool_name == "voice.continuity_reset":
        if not bool(arguments.get("confirm")):
            return build_control_page_voice_continuity_reset_required_reply()
        reset_continuity_probe(reason=clean_text(str(arguments.get("reason") or "")))
        return build_control_page_voice_continuity_reset_reply(build_voice_continuity_reply(guild))
    return None


async def execute_control_page_minecraft_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    guild: Any,
    build_inventory_reply: Any,
    build_minecraft_reply: Any,
    enable_mode: Any,
    disable_mode: Any,
    get_client: Any,
    format_position: Any,
) -> str | None:
    if not tool_name.startswith("minecraft."):
        return None
    if guild is None:
        return control_page_discord_required_reply()
    if tool_name == "minecraft.inventory":
        return await build_inventory_reply(guild)
    if tool_name == "minecraft.status":
        return await build_minecraft_reply(guild)
    if tool_name == "minecraft.connect":
        observed = await enable_mode(guild.id)
        return build_control_page_minecraft_connect_reply_payload(
            observed,
            position_text=format_position(observed.get("position")),
        )
    if tool_name == "minecraft.disconnect":
        await disable_mode(guild.id)
        return build_control_page_minecraft_disconnect_reply()
    if tool_name == "minecraft.set_goal":
        goal_text = clean_text(str(arguments.get("goal") or ""))
        if not goal_text:
            return build_control_page_minecraft_goal_missing_reply()
        status = await get_client().set_goal(goal_text)
        return build_control_page_minecraft_goal_updated_reply(goal_text, status)
    return None


def build_control_page_autonomy_reply_payload(
    *,
    status: str,
    safety_mode: str,
    goal: str,
    plan: str,
    drive: dict[str, Any] | None,
    failure_count: int,
    last_error: str | None,
    minecraft_enabled: bool,
    allowed_actions: list[str],
) -> str:
    drive_payload = drive if isinstance(drive, dict) else {}
    drive_line = (
        f"mood={clean_text(str(drive_payload.get('mood') or 'unknown'))} "
        f"impulse={clean_text(str(drive_payload.get('last_impulse') or 'unknown'))} "
        f"gate={clean_text(str(drive_payload.get('last_gate_reason') or 'unknown'))} "
        f"curiosity={float(drive_payload.get('curiosity', 0.0) or 0.0):.2f} "
        f"concern={float(drive_payload.get('concern', 0.0) or 0.0):.2f} "
        f"restraint={float(drive_payload.get('restraint', 0.0) or 0.0):.2f}"
    ) if drive_payload else "not initialized"
    allowed = ", ".join(list(allowed_actions or [])[:6]) or "없음"
    if len(allowed_actions or []) > 6:
        allowed += ", ..."
    return "\n".join(
        [
            "자율 행동 상태",
            f"- 상태: {clean_text(status)}",
            f"- 안전 모드: {clean_text(safety_mode)}",
            f"- 목표: {clean_text(goal) or '없음'}",
            f"- 계획: {clean_text(plan) or '없음'}",
            f"- drive: {drive_line}",
            f"- 실패 횟수: {int(failure_count)}",
            f"- 마지막 오류: {clean_text(last_error or '') or '없음'}",
            f"- Minecraft 자율 행동: {command_status(minecraft_enabled)}",
            f"- 허용 액션: {allowed}",
        ]
    )


__all__ = [
    "ControlPageChatLogStore",
    "ControlPageMinecraftSnapshotCache",
    "ControlPageRuntimeServicesCache",
    "ControlPageUiCommandStore",
    "build_control_page_autonomy_reply_payload",
    "build_control_page_boot_progress_payload",
    "build_control_page_guild_state_payload",
    "build_control_page_inventory_reply_payload",
    "build_control_page_local_state_payload",
    "build_control_page_local_status_text_payload",
    "build_control_page_minecraft_connect_reply_payload",
    "build_control_page_minecraft_disconnect_reply",
    "build_control_page_minecraft_goal_missing_reply",
    "build_control_page_minecraft_goal_updated_reply",
    "build_control_page_minecraft_payload",
    "build_control_page_minecraft_reply_payload",
    "build_control_page_runtime_diagnostics",
    "build_control_page_runtime_services_error_payload",
    "build_control_page_runtime_services_payload",
    "build_control_page_runtime_payload",
    "build_control_page_status_text_payload",
    "build_control_page_shutdown_reply",
    "build_control_page_shutdown_tool_reply",
    "build_control_page_ui_state",
    "build_control_page_voice_continuity_reply_payload",
    "build_control_page_voice_continuity_reset_reply",
    "build_control_page_voice_continuity_reset_required_reply",
    "build_control_page_voice_input_mode_reply",
    "build_control_page_voice_payload",
    "build_control_page_voice_reconnect_reply",
    "build_control_page_voice_status_reply_payload",
    "command_status",
    "control_page_discord_required_reply",
    "control_page_chat_refresh_plan",
    "control_page_open_memory_vault_payload",
    "control_page_open_memory_vault_result",
    "control_page_open_memory_vault_tool_reply",
    "control_page_query_flag",
    "control_page_result_status",
    "execute_control_page_memory_tool",
    "execute_control_page_minecraft_tool",
    "execute_control_page_runtime_tool",
    "execute_control_page_voice_tool",
    "handle_control_page_chat_request",
    "handle_control_page_memory_note_action_request",
    "handle_control_page_shutdown_request",
    "is_control_page_minecraft_session_active",
    "memory_vault_obsidian_url",
    "memory_vault_open_tool_reply",
    "parse_control_page_chat_payload",
    "parse_control_page_guild_id",
    "parse_control_page_memory_graph_query",
    "parse_control_page_memory_note_action_payload",
    "parse_control_page_memory_note_query",
    "parse_control_page_memory_snapshot_query",
    "sanitize_control_page_welcome_text_payload",
]
