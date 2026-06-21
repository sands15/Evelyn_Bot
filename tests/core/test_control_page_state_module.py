from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_state import (  # noqa: E402
    ControlPageChatLogStore,
    ControlPageMinecraftSnapshotCache,
    ControlPageRuntimeServicesCache,
    ControlPageUiCommandStore,
    build_control_page_autonomy_reply_payload,
    build_control_page_boot_progress_payload,
    build_control_page_guild_state_payload,
    build_control_page_guild_state_view,
    build_control_page_inventory_reply_payload,
    build_control_page_local_state_payload,
    build_control_page_local_state_view,
    build_control_page_local_status_text_payload,
    build_control_page_minecraft_payload,
    build_control_page_minecraft_connect_reply_payload,
    build_control_page_minecraft_disconnect_reply,
    build_control_page_minecraft_goal_missing_reply,
    build_control_page_minecraft_goal_updated_reply,
    build_control_page_minecraft_reply_payload,
    build_control_page_runtime_payload,
    build_control_page_runtime_diagnostics,
    build_control_page_runtime_services_error_payload,
    build_control_page_runtime_services_payload,
    build_control_page_shutdown_reply,
    build_control_page_shutdown_tool_reply,
    build_control_page_status_text_payload,
    build_control_page_ui_state,
    build_control_page_voice_continuity_reply_payload,
    build_control_page_voice_continuity_reset_reply,
    build_control_page_voice_continuity_reset_required_reply,
    build_control_page_voice_input_mode_reply,
    build_control_page_voice_payload,
    build_control_page_voice_reconnect_reply,
    build_control_page_voice_status_reply_payload,
    control_page_chat_refresh_plan,
    control_page_discord_required_reply,
    control_page_open_memory_vault_payload,
    control_page_open_memory_vault_result,
    control_page_open_memory_vault_tool_reply,
    control_page_query_flag,
    control_page_result_status,
    handle_control_page_chat_request,
    handle_control_page_memory_note_action_request,
    handle_control_page_shutdown_request,
    is_control_page_minecraft_session_active,
    memory_vault_obsidian_url,
    memory_vault_open_tool_reply,
    parse_control_page_chat_payload,
    parse_control_page_guild_id,
    parse_control_page_memory_graph_query,
    parse_control_page_memory_note_action_payload,
    parse_control_page_memory_note_query,
    parse_control_page_memory_snapshot_query,
    sanitize_control_page_welcome_text_payload,
)


class ControlPageStateModuleTests(unittest.TestCase):
    def test_chat_log_store_sanitizes_defaults_and_trims(self) -> None:
        store = ControlPageChatLogStore(limit=2)

        store.append(1, "user", "", " hi ", now=10.0)
        store.append(1, "assistant", "", " answer ", now=11.0)
        store.append(1, "user", "정훈", " next ", now=12.0)
        store.append(1, "user", "정훈", "   ", now=13.0)

        self.assertEqual(
            store.get(1),
            [
                {"role": "assistant", "author": "Evelyn", "text": "answer", "at": 11.0},
                {"role": "user", "author": "정훈", "text": "next", "at": 12.0},
            ],
        )

    def test_ui_command_store_tracks_revision_and_trims(self) -> None:
        store = ControlPageUiCommandStore(limit=2)

        first = store.enqueue(" Open ", panel_id="memory", now=1.0)
        store.enqueue("close", panel_id="memory", now=2.0)
        store.enqueue("toggle", panel_id="memory", now=3.0)
        panel_state = store.panel_state()

        self.assertEqual(first, {"id": 1, "action": "open", "panel": "memory", "at": 1.0})
        self.assertEqual(panel_state["revision"], 3)
        self.assertEqual(
            panel_state["commands"],
            [
                {"id": 2, "action": "close", "panel": "memory", "at": 2.0},
                {"id": 3, "action": "toggle", "panel": "memory", "at": 3.0},
            ],
        )

    def test_minecraft_snapshot_cache_tracks_fresh_stale_expired_and_errors(self) -> None:
        cache = ControlPageMinecraftSnapshotCache(stale_after_sec=5.0, expired_after_sec=10.0)

        self.assertFalse(cache.has_snapshot())
        self.assertFalse(cache.is_fresh(now=100.0))

        fresh = cache.store_success({"inventory_top": [{"name": "stone", "count": 3}]}, now=100.0)
        self.assertTrue(cache.has_snapshot())
        self.assertTrue(cache.is_fresh(now=104.0))
        self.assertEqual(fresh["snapshot_age_sec"], 0.0)

        stale = cache.snapshot_copy(now=106.0)
        self.assertTrue(stale["snapshot_stale"])
        self.assertFalse(stale["snapshot_expired"])
        self.assertEqual(stale["snapshot_age_sec"], 6.0)

        expired = cache.snapshot_copy(now=111.0)
        self.assertTrue(expired["snapshot_expired"])
        self.assertEqual(expired["inventory_summary"], "inventory unavailable")
        self.assertEqual(expired["inventory_top"], [])

        errored = cache.store_error("timeout")
        self.assertEqual(errored["last_error"], "timeout")
        self.assertTrue(errored["snapshot_stale"])

    def test_runtime_services_cache_tracks_age_and_refresh_throttle(self) -> None:
        cache = ControlPageRuntimeServicesCache(
            stale_after_sec=5.0,
            expired_after_sec=10.0,
            refresh_min_interval_sec=2.0,
        )

        self.assertFalse(cache.has_services())
        self.assertFalse(cache.is_fresh(now=100.0))

        fresh = cache.store_success({"botReady": True}, now=100.0)
        self.assertTrue(cache.has_services())
        self.assertTrue(cache.is_fresh(now=104.0))
        self.assertEqual(fresh["runtimeStatusAgeSec"], 0.0)
        self.assertFalse(fresh["runtimeStatusStale"])

        stale = cache.snapshot_copy(refreshing=True, now=106.0)
        self.assertTrue(cache.is_stale_not_expired(now=106.0))
        self.assertTrue(stale["runtimeStatusRefreshing"])
        self.assertTrue(stale["runtimeStatusStale"])
        self.assertFalse(stale["runtimeStatusExpired"])

        expired = cache.snapshot_copy(now=111.0)
        self.assertFalse(cache.is_stale_not_expired(now=111.0))
        self.assertTrue(expired["runtimeStatusExpired"])

        self.assertTrue(cache.can_schedule_refresh(refreshing=False, now=110.0))
        cache.mark_refresh_request(now=110.0)
        self.assertFalse(cache.can_schedule_refresh(refreshing=False, now=111.0))
        self.assertFalse(cache.can_schedule_refresh(refreshing=True, now=120.0))
        self.assertTrue(cache.can_schedule_refresh(refreshing=False, now=113.0))

    def test_runtime_services_payload_builders_keep_readiness_summary_contract(self) -> None:
        services = build_control_page_runtime_services_payload(
            service_results={"main": True, "router": False, "sub": True, "tts": True},
            voyager_ready=True,
            voyager_error="",
            bot_api_port_open=True,
            bot_api_http_ready=True,
            bot_api_state="partial",
            bot_api_reason="",
            bot_api_error="state warming",
            bot_api_error_kind="",
            codex_required=True,
            codex_ready=False,
            codex_backend="codex-gateway",
            codex_error="login required",
        )
        errored = build_control_page_runtime_services_error_payload(
            "probe failed",
            action_backend="codex-gateway",
        )

        self.assertFalse(services["botReady"])
        self.assertTrue(services["mainReady"])
        self.assertEqual(services["botApiReason"], "CP_BOT_STATE_NOT_READY")
        self.assertEqual(services["codexBackend"], "codex-gateway")
        self.assertEqual(services["codexError"], "login required")
        self.assertIn("bot down", services["summary"])
        self.assertEqual(errored["botApiReason"], "CP_RUNTIME_REFRESH_ERROR")
        self.assertEqual(errored["botApiError"], "probe failed")
        self.assertTrue(errored["codexRequired"])

    def test_welcome_text_sanitizer_trims_quotes_and_falls_back(self) -> None:
        self.assertEqual(
            sanitize_control_page_welcome_text_payload("  \"어서 와\"  ", fallback="fallback"),
            "어서 와",
        )
        self.assertEqual(sanitize_control_page_welcome_text_payload("", fallback="fallback"), "fallback")
        self.assertTrue(
            sanitize_control_page_welcome_text_payload("a" * 140, fallback="fallback").endswith("...")
        )

    def test_minecraft_session_active_uses_live_snapshot_signals(self) -> None:
        self.assertFalse(is_control_page_minecraft_session_active({}))
        self.assertTrue(is_control_page_minecraft_session_active({"voyager_connected": True}))
        self.assertTrue(is_control_page_minecraft_session_active({"position": {"x": 1, "y": None, "z": None}}))
        self.assertTrue(is_control_page_minecraft_session_active({"health": 20}))

    def test_ui_state_prioritizes_live_minecraft_over_voice(self) -> None:
        state = build_control_page_ui_state(
            guild_available=True,
            listening=True,
            speaking=True,
            minecraft_running=True,
            minecraft_session_active=True,
            minecraft_snapshot_stale=True,
            minecraft_last_error="old error",
        )

        self.assertEqual(state["mode"], "minecraft")
        self.assertEqual(state["reason"], "minecraft_session_active")

    def test_boot_progress_treats_ready_services_as_warmup_done(self) -> None:
        progress = build_control_page_boot_progress_payload(
            {"mainReady": True, "ttsReady": True, "botApiHttpReady": True},
            startup_steps=(
                ("main_service", "Main LLM"),
                ("main_warmup", "Main LLM warmup"),
                ("tts_warmup", "TTS warmup"),
                ("control_api", "Bot API"),
            ),
            startup_component_state={
                "main_warmup": {"status": "failed", "detail": "stale failure"},
                "tts_warmup": {"status": "failed", "detail": "stale failure"},
            },
            startup_components_ready=False,
            discord_enabled=False,
            discord_ready=False,
            guild_available=True,
            control_api_available=False,
        )

        self.assertEqual(progress["percent"], 100)
        self.assertTrue(progress["ready"])
        self.assertEqual([step["status"] for step in progress["steps"]], ["done", "done", "done", "done"])

    def test_runtime_diagnostics_keeps_control_api_ready_external(self) -> None:
        diagnostics = build_control_page_runtime_diagnostics(
            {
                "botApiPortOpen": True,
                "botApiState": "partial",
                "botApiReason": "CP_BOT_PROXY_TIMEOUT",
                "botApiError": "timeout",
            },
            control_api_ready=False,
        )

        self.assertFalse(diagnostics["controlApiReady"])
        self.assertTrue(diagnostics["botApiPortOpen"])
        self.assertEqual(diagnostics["botApiState"], "partial")
        self.assertEqual(diagnostics["botApiReason"], "CP_BOT_PROXY_TIMEOUT")

    def test_payload_builders_keep_frontend_contract_keys(self) -> None:
        panels = {"revision": 3, "commands": [], "panels": [{"id": "memory", "label": "Memory"}]}
        voice = build_control_page_voice_payload(
            channel_name="General",
            listening=True,
            speaking=False,
            tts_target_name="없음",
        )
        runtime = build_control_page_runtime_payload(
            main_model="main",
            router_model="router",
            summary_model="summary",
            stt_model="stt",
            inflight_llm_requests=2,
            tts_backlog=1,
            output_mode="discord_voice",
            local_tts_output={"active": False},
            model_call_metrics={},
            question_metrics={},
            local_mic={},
            voice_pipeline={},
            vision_watch={},
            services={},
            diagnostics={},
            service_health={},
            control_page_panels=panels,
            boot_progress={"ready": True},
            voice_debug_audio=True,
            local_mic_target={"kind": "discord"},
        )
        minecraft = build_control_page_minecraft_payload(
            {
                "minecraft_autonomy": True,
                "voyager_connected": False,
                "inventory_top": [{"name": "stone", "count": 3}],
            },
            minecraft_session_active=False,
            minecraft_status_fields={"runtimeFreshness": "fresh"},
        )

        local_state = build_control_page_local_state_payload(
            ok=True,
            generated_at=1.0,
            local_url="http://127.0.0.1:8799/",
            boot_progress={"ready": True},
            ui_state={"mode": "default"},
            guild_id=0,
            guild_name="Evelyn Local",
            commands=[],
            all_commands=[],
            chat_messages=[],
            control_page_panels=panels,
            voice=voice,
            runtime=runtime,
            status_text="local status",
        )
        guild_state = build_control_page_guild_state_payload(
            generated_at=2.0,
            local_url="http://127.0.0.1:8799/",
            boot_progress={"ready": True},
            ui_state={"mode": "minecraft"},
            guild_id=123,
            guild_name="Guild",
            commands=[],
            all_commands=[],
            chat_messages=[],
            control_page_panels=panels,
            voice=voice,
            runtime=runtime,
            minecraft=minecraft,
            status_text="guild status",
        )

        self.assertIn("controlPagePanels", local_state)
        self.assertIn("controlPagePanels", local_state["runtime"])
        self.assertEqual(local_state["statusText"], "local status")
        self.assertEqual(guild_state["minecraft"]["runtimeFreshness"], "fresh")
        self.assertIn("idleSummary", guild_state["minecraft"])
        self.assertTrue(guild_state["runtime"]["voiceDebugAudio"])

    def test_state_view_builders_compose_runtime_voice_and_minecraft_payloads(self) -> None:
        common_runtime = {
            "runtime_services": {"mainReady": True, "botApiHttpReady": True, "botApiState": "ready"},
            "runtime_diagnostics": {"controlApiReady": True},
            "runtime_health": {"services": []},
            "boot_progress": {"ready": True},
            "panel_state": {"revision": 1, "commands": []},
            "main_model": "main",
            "router_model": "router",
            "summary_model": "summary",
            "stt_model": "stt",
            "inflight_llm_requests": 1,
            "tracked_tts_count": 2,
            "model_call_metrics": {"count": 1},
            "question_metrics": {"count": 2},
            "voice_pipeline": {"listening": True},
            "vision_watch": {"status": "idle"},
        }

        local_state = build_control_page_local_state_view(
            generated_at=10.0,
            local_url="http://127.0.0.1:8799/",
            local_mode=True,
            local_guild_id=0,
            local_guild_name="Evelyn Local",
            commands=[],
            all_commands=[],
            chat_messages=[],
            local_tts={"active": True},
            local_mic={"enabled": True, "captureReady": True},
            local_listening=True,
            output_mode="local_speaker",
            status_text="local ok",
            **common_runtime,
        )
        guild_state = build_control_page_guild_state_view(
            generated_at=11.0,
            local_url="http://127.0.0.1:8799/",
            guild_id=123,
            guild_name="Guild",
            voice_channel_name="General",
            listening=True,
            speaking=True,
            tts_target_name="정훈",
            commands=[],
            all_commands=[],
            chat_messages=[],
            local_tts={"active": True},
            local_mic={"enabled": False},
            output_mode="discord_voice",
            minecraft={"minecraft_autonomy": True, "voyager_connected": True, "inventory_top": []},
            minecraft_session_active=True,
            minecraft_status_fields={"snapshotFreshness": "fresh"},
            voice_debug_audio=True,
            local_mic_target={"kind": "discord"},
            status_text="guild ok",
            **common_runtime,
        )

        self.assertEqual(local_state["ui"]["reason"], "voice_listening")
        self.assertEqual(local_state["voice"]["ttsTargetName"], "로컬 스피커")
        self.assertEqual(local_state["runtime"]["ttsBacklog"], 3)
        self.assertEqual(local_state["statusText"], "local ok")
        self.assertEqual(guild_state["ui"]["mode"], "minecraft")
        self.assertEqual(guild_state["voice"]["channelName"], "General")
        self.assertEqual(guild_state["runtime"]["outputMode"], "discord_voice")
        self.assertTrue(guild_state["runtime"]["voiceDebugAudio"])
        self.assertEqual(guild_state["runtime"]["localMicTarget"], {"kind": "discord"})
        self.assertTrue(guild_state["minecraft"]["sessionActive"])

    def test_chat_refresh_plan_marks_minecraft_commands_for_cache_refresh(self) -> None:
        minecraft_plan = control_page_chat_refresh_plan(" /minecraft status ")
        inventory_plan = control_page_chat_refresh_plan("/inventory")
        plain_plan = control_page_chat_refresh_plan("그냥 대화")

        self.assertEqual(minecraft_plan["normalized"], "/minecraft status")
        self.assertTrue(minecraft_plan["needs_fresh_snapshot"])
        self.assertTrue(minecraft_plan["needs_runtime_refresh"])
        self.assertTrue(inventory_plan["needs_fresh_snapshot"])
        self.assertFalse(inventory_plan["needs_runtime_refresh"])
        self.assertFalse(plain_plan["needs_fresh_snapshot"])
        self.assertFalse(plain_plan["needs_runtime_refresh"])

    def test_route_request_parsers_normalize_control_page_inputs(self) -> None:
        self.assertEqual(parse_control_page_guild_id(" 123 "), 123)
        self.assertIsNone(parse_control_page_guild_id("bad"))
        self.assertTrue(control_page_query_flag("true"))
        self.assertTrue(control_page_query_flag("on"))
        self.assertFalse(control_page_query_flag("no"))

        self.assertEqual(
            parse_control_page_memory_graph_query({"max_nodes": "25", "include_internal": "true"}),
            {"max_nodes": 25, "include_internal": True},
        )
        self.assertEqual(
            parse_control_page_memory_graph_query({"max_nodes": "bad"}),
            {"max_nodes": 160, "include_internal": False},
        )
        self.assertEqual(
            parse_control_page_memory_snapshot_query({"include_hidden": "1", "include_internal": "yes", "limit": "12"}),
            {"include_hidden": True, "include_internal": True, "limit": 12},
        )
        self.assertEqual(parse_control_page_memory_note_query({"include_internal": "true"}), {"include_internal": True})
        self.assertEqual(
            parse_control_page_memory_note_action_payload({"action": " edit ", "title": "Title", "body": "Body"}),
            {"action": "edit", "title": "Title", "body": "Body"},
        )
        self.assertEqual(
            parse_control_page_chat_payload({"text": " hi ", "guildId": "7"}),
            {"ok": True, "error": "", "status": 200, "text": "hi", "guild_id": 7},
        )
        self.assertEqual(parse_control_page_chat_payload({})["error"], "empty_text")

    def test_memory_note_action_handler_helper_parses_payload_and_status(self) -> None:
        calls: list[tuple[str, str, str, str]] = []

        def update_note(note_id: str, action: str, *, title: str | None = None, body: str | None = None) -> dict:
            calls.append((note_id, action, title or "", body or ""))
            return {"ok": action == "edit", "noteId": note_id}

        ok_result, ok_status = handle_control_page_memory_note_action_request(
            "note-1",
            {"action": " edit ", "title": "Title", "body": "Body"},
            update_note=update_note,
        )
        fail_result, fail_status = handle_control_page_memory_note_action_request(
            "note-2",
            {"action": "missing"},
            update_note=update_note,
        )

        self.assertEqual(ok_status, 200)
        self.assertEqual(fail_status, 404)
        self.assertTrue(ok_result["ok"])
        self.assertFalse(fail_result["ok"])
        self.assertEqual(
            calls,
            [
                ("note-1", "edit", "Title", "Body"),
                ("note-2", "missing", "", ""),
            ],
        )
        self.assertEqual(control_page_result_status({"ok": True}), 200)
        self.assertEqual(control_page_result_status({"ok": False}, error_status=409), 409)

    def test_control_page_chat_request_handler_orchestrates_logs_refresh_and_state(self) -> None:
        guild = SimpleNamespace(id=7)
        logs: list[tuple[int, str, str, str]] = []
        snapshots: list[tuple[int, bool, bool]] = []
        refreshes: list[bool] = []

        async def handle_input(_guild, text: str) -> str:
            return f"reply:{text}"

        async def ensure_snapshot(guild_id: int, *, force: bool, wait: bool) -> None:
            snapshots.append((guild_id, force, wait))

        async def refresh_runtime_services(*, force: bool = False) -> dict:
            refreshes.append(force)
            return {"ok": True}

        async def build_state(_guild) -> dict:
            return {"state": "ok"}

        payload, status = asyncio.run(
            handle_control_page_chat_request(
                {"text": " /minecraft status ", "guildId": "7"},
                discord_enabled=True,
                select_guild=lambda guild_id: guild if guild_id == 7 else None,
                effective_guild_id=lambda value: value.id if value is not None else 0,
                append_chat_log=lambda *args: logs.append(args),
                handle_input=handle_input,
                ensure_minecraft_snapshot=ensure_snapshot,
                refresh_runtime_services=refresh_runtime_services,
                build_state=build_state,
            )
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True, "reply": "reply:/minecraft status", "state": {"state": "ok"}})
        self.assertEqual(logs[0], (7, "user", "정훈", "/minecraft status"))
        self.assertEqual(logs[1], (7, "assistant", "Evelyn", "reply:/minecraft status"))
        self.assertEqual(snapshots, [(7, True, True)])
        self.assertEqual(refreshes, [True])

    def test_control_page_chat_request_handler_reports_invalid_and_missing_guild(self) -> None:
        async def noop(*args, **kwargs):
            return {}

        empty_payload, empty_status = asyncio.run(
            handle_control_page_chat_request(
                {},
                discord_enabled=False,
                select_guild=lambda _guild_id: None,
                effective_guild_id=lambda _guild: 0,
                append_chat_log=lambda *args: None,
                handle_input=noop,
                ensure_minecraft_snapshot=noop,
                refresh_runtime_services=noop,
                build_state=noop,
            )
        )
        missing_payload, missing_status = asyncio.run(
            handle_control_page_chat_request(
                {"text": "hi", "guildId": "99"},
                discord_enabled=True,
                select_guild=lambda _guild_id: None,
                effective_guild_id=lambda _guild: 0,
                append_chat_log=lambda *args: None,
                handle_input=noop,
                ensure_minecraft_snapshot=noop,
                refresh_runtime_services=noop,
                build_state=noop,
            )
        )

        self.assertEqual(empty_status, 400)
        self.assertEqual(empty_payload["error"], "empty_text")
        self.assertEqual(missing_status, 503)
        self.assertEqual(missing_payload["error"], "guild_not_available")

    def test_control_page_shutdown_request_handler_routes_shutdown_command(self) -> None:
        guild = SimpleNamespace(id=7)
        handled: list[tuple[object, str]] = []

        async def handle_input(selected_guild, text: str) -> str:
            handled.append((selected_guild, text))
            return "shutdown started"

        async def build_state(selected_guild) -> dict:
            return {"guild": selected_guild.id if selected_guild is not None else None}

        payload, status = asyncio.run(
            handle_control_page_shutdown_request(
                "7",
                select_guild=lambda guild_id: guild if guild_id == 7 else None,
                handle_input=handle_input,
                build_state=build_state,
            )
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True, "reply": "shutdown started", "state": {"guild": 7}})
        self.assertEqual(handled, [(guild, "/shutdown")])

    def test_memory_vault_open_helpers_build_tool_replies_and_http_payloads(self) -> None:
        url = memory_vault_obsidian_url("C:/Evelyn Memory/Vault")
        obsidian_payload = control_page_open_memory_vault_payload(
            vault_path="C:/Evelyn Memory/Vault",
            obsidian_url=url,
            outcome="obsidian",
        )
        folder_payload = control_page_open_memory_vault_payload(
            vault_path="C:/Evelyn Memory/Vault",
            obsidian_url=url,
            outcome="folder",
            error="protocol blocked",
        )
        failed_payload = control_page_open_memory_vault_payload(
            vault_path="C:/Evelyn Memory/Vault",
            obsidian_url=url,
            outcome="failed",
            error="no opener",
        )

        self.assertEqual(url, "obsidian://open?path=C%3A%2FEvelyn%20Memory%2FVault")
        self.assertEqual(memory_vault_open_tool_reply(outcome="obsidian"), "Obsidian 메모리 vault를 열게.")
        self.assertIn("vault 폴더를 대신 열었어", memory_vault_open_tool_reply(outcome="folder", error="blocked"))
        self.assertIn("열지 못했어", memory_vault_open_tool_reply(outcome="failed", error="no opener"))
        self.assertTrue(obsidian_payload["ok"])
        self.assertEqual(folder_payload["fallback"], "folder")
        self.assertFalse(failed_payload["ok"])
        self.assertEqual(failed_payload["error"], "open_memory_vault_failed")

    def test_memory_vault_open_result_uses_obsidian_then_folder_fallback(self) -> None:
        calls: list[tuple[str, str]] = []

        obsidian_payload, obsidian_status = control_page_open_memory_vault_result(
            vault_path="C:/Vault",
            obsidian_url="obsidian://open?path=C%3A%2FVault",
            open_url=lambda url: calls.append(("url", str(url))),
            open_path=lambda path: calls.append(("path", str(path))),
        )
        self.assertEqual(obsidian_status, 200)
        self.assertTrue(obsidian_payload["ok"])
        self.assertEqual(calls, [("url", "obsidian://open?path=C%3A%2FVault")])

        calls.clear()

        def fail_url(url: str) -> None:
            calls.append(("url", url))
            raise RuntimeError("protocol blocked")

        folder_payload, folder_status = control_page_open_memory_vault_result(
            vault_path="C:/Vault",
            obsidian_url="obsidian://open?path=C%3A%2FVault",
            open_url=fail_url,
            open_path=lambda path: calls.append(("path", str(path))),
        )
        self.assertEqual(folder_status, 200)
        self.assertEqual(folder_payload["fallback"], "folder")
        self.assertEqual(calls, [("url", "obsidian://open?path=C%3A%2FVault"), ("path", "C:/Vault")])

        def fail_path(path: str) -> None:
            calls.append(("path", path))
            raise RuntimeError("no opener")

        failed_payload, failed_status = control_page_open_memory_vault_result(
            vault_path="C:/Vault",
            obsidian_url="obsidian://open?path=C%3A%2FVault",
            open_url=fail_url,
            open_path=fail_path,
        )
        self.assertEqual(failed_status, 500)
        self.assertFalse(failed_payload["ok"])
        self.assertEqual(failed_payload["error"], "open_memory_vault_failed")

    def test_memory_vault_open_tool_reply_uses_same_fallback_order(self) -> None:
        calls: list[tuple[str, str]] = []

        def fail_url(url: str) -> None:
            calls.append(("url", url))
            raise RuntimeError("protocol blocked")

        def ok_path(path: str) -> None:
            calls.append(("path", path))

        reply = control_page_open_memory_vault_tool_reply(
            vault_path="C:/Vault",
            obsidian_url="obsidian://open?path=C%3A%2FVault",
            open_url=fail_url,
            open_path=ok_path,
        )

        self.assertIn("vault 폴더를 대신 열었어", reply)
        self.assertEqual(calls, [("url", "obsidian://open?path=C%3A%2FVault"), ("path", "C:/Vault")])

        def fail_path(path: str) -> None:
            calls.append(("path", path))
            raise RuntimeError("no opener")

        failed_reply = control_page_open_memory_vault_tool_reply(
            vault_path="C:/Vault",
            obsidian_url="obsidian://open?path=C%3A%2FVault",
            open_url=fail_url,
            open_path=fail_path,
        )

        self.assertIn("열지 못했어", failed_reply)

    def test_status_text_payloads_format_live_state_summaries(self) -> None:
        guild_text = build_control_page_status_text_payload(
            guild_name="Guild",
            voice_channel_name="General",
            listening=True,
            speaking=False,
            tts_target="없음",
            voice_input_mode="입력 모드",
            local_mic_status="로컬 마이크",
            main_model="main",
            router_model="router",
            summary_model="summary",
            stt_model="stt",
            minecraft={
                "minecraft_autonomy": True,
                "voyager_connected": False,
                "snapshot_freshness": "fresh",
                "snapshot_age_sec": 3,
                "current_task": "mine",
                "goal": "diamond",
            },
        )
        local_text = build_control_page_local_status_text_payload(
            {
                "botApiHttpReady": True,
                "botApiState": "ready",
                "botApiPortOpen": True,
                "mainReady": True,
                "routerReady": True,
                "subReady": True,
                "ttsReady": True,
                "voyagerReady": False,
                "codexRequired": True,
                "codexReady": False,
                "summary": "all good",
            },
            discord_enabled=False,
            local_url="http://127.0.0.1:8799/",
            bot_api_host="127.0.0.1",
            bot_api_port=8798,
            main_model="main",
            router_model="router",
            summary_model="summary",
            stt_model="stt",
            local_speaking=False,
            local_listening=True,
            local_mic_status="마이크 준비",
        )

        self.assertIn("- 서버: Guild", guild_text)
        self.assertIn("- Voyager 실행: 켜짐", guild_text)
        self.assertIn("- 스냅샷: fresh (3s)", guild_text)
        self.assertIn("Evelyn 로컬 상태", local_text)
        self.assertIn("- Bot API 상태: 켜짐 (ready)", local_text)
        self.assertIn("- Codex gateway: 꺼짐", local_text)

    def test_control_page_command_reply_payloads_format_voice_and_minecraft(self) -> None:
        voice_text = build_control_page_voice_status_reply_payload(
            {
                "lastVoiceChannel": {"channel_name": "Saved"},
                "localTtsOutput": {"enabled": True, "active": False, "device": "Speaker"},
                "outputMode": "discord_voice",
                "queueDepth": 1,
                "queueMax": 16,
                "liveRecent": True,
                "sttBusy": False,
                "sttCooldownRemainingSec": 0,
                "sttTimeoutCount": 2,
                "queueFullDropCount": 3,
                "queueStaleDropCount": 4,
                "llmFailedCount": 5,
                "ttsRequestFailedCount": 6,
                "ttsPlaybackFailedCount": 7,
                "voiceDeliveryFailedCount": 8,
                "sttMsP95": 11,
                "ttsFirstAudioMsP95": 22,
                "mainFirstTokenMsP95": 33,
            },
            channel_name="General",
            voice_input_mode="auto",
            local_mic_status="ready",
            continuity_detail_lines=["- 연속 성공: 2/5"],
        )
        continuity_text = build_control_page_voice_continuity_reply_payload(["- 연속 성공: 2/5"])
        inventory_text = build_control_page_inventory_reply_payload(
            {"inventory_top": [{"name": "stone", "count": 3}]}
        )
        minecraft_text = build_control_page_minecraft_reply_payload(
            {
                "minecraft_autonomy": True,
                "voyager_connected": True,
                "goal": "diamond",
                "current_task": "mine",
                "voyager_tech_tree_highest": "iron",
                "voyager_unique_item_count": 12,
                "position_text": "1, 2, 3",
            }
        )

        self.assertIn("음성 상태", voice_text)
        self.assertIn("- 현재 채널: General", voice_text)
        self.assertIn("- p95: stt=11ms tts_first=22ms main_first=33ms", voice_text)
        self.assertIn("- 연속 성공: 2/5", voice_text)
        self.assertEqual(continuity_text, "바리인 연속성\n- 연속 성공: 2/5")
        self.assertEqual(inventory_text, "Minecraft 인벤토리 요약\n- stone: 3")
        self.assertIn("- Voyager 실행: 켜짐", minecraft_text)
        self.assertIn("- 목표: diamond", minecraft_text)
        self.assertIn("- position: 1, 2, 3", minecraft_text)

    def test_autonomy_reply_payload_formats_drive_and_allowed_actions(self) -> None:
        text = build_control_page_autonomy_reply_payload(
            status="running",
            safety_mode="normal",
            goal="explore",
            plan="look around",
            drive={
                "mood": "curious",
                "last_impulse": "inspect",
                "last_gate_reason": "ok",
                "curiosity": 0.5,
                "concern": 0.25,
                "restraint": 0.75,
            },
            failure_count=2,
            last_error="",
            minecraft_enabled=True,
            allowed_actions=["observe", "speak", "move", "search", "remember", "wait", "extra"],
        )

        self.assertIn("자율 행동 상태", text)
        self.assertIn("- 상태: running", text)
        self.assertIn("- drive: mood=curious impulse=inspect gate=ok curiosity=0.50 concern=0.25 restraint=0.75", text)
        self.assertIn("- Minecraft 자율 행동: 켜짐", text)
        self.assertIn("- 허용 액션: observe, speak, move, search, remember, wait, ...", text)

    def test_control_page_tool_execution_reply_payloads(self) -> None:
        self.assertEqual(control_page_discord_required_reply(), "그 명령은 Discord 연결이 필요해.")
        self.assertEqual(
            build_control_page_voice_input_mode_reply(voice_input_mode="자동", mode="auto"),
            "음성 입력 모드: 자동 (auto)",
        )
        self.assertEqual(
            build_control_page_voice_reconnect_reply(ok=True, detail="General"),
            "음성 채널에 다시 연결했어: General",
        )
        self.assertEqual(
            build_control_page_voice_reconnect_reply(ok=False, detail="missing"),
            "음성 재연결 실패: missing",
        )
        self.assertEqual(
            build_control_page_voice_continuity_reset_required_reply(),
            "바리인 연속성 리셋은 확인이 필요해. `/voice continuity reset confirm`로 실행해줘.",
        )
        self.assertEqual(
            build_control_page_voice_continuity_reset_reply("바리인 연속성\n- 연속 성공: 0/5"),
            "바리인 연속성 카운터를 리셋했어.\n바리인 연속성\n- 연속 성공: 0/5",
        )
        self.assertEqual(
            build_control_page_shutdown_reply(local_mode=True, helper_started=True),
            "Local Evelyn shutdown started. Only Evelyn local runtime windows and ports will be stopped.",
        )
        self.assertEqual(
            build_control_page_shutdown_reply(local_mode=False, helper_started=False),
            "종료 helper 실행에 실패해서 bot process만 정리할게.",
        )
        scheduled: list[str] = []
        self.assertEqual(
            build_control_page_shutdown_tool_reply(
                guild_available=False,
                schedule_local_shutdown=lambda: True,
                schedule_stack_shutdown=lambda: False,
                schedule_bot_shutdown=lambda: scheduled.append("bot"),
            ),
            "Local Evelyn shutdown started. Only Evelyn local runtime windows and ports will be stopped.",
        )
        self.assertEqual(scheduled, [])
        self.assertEqual(
            build_control_page_shutdown_tool_reply(
                guild_available=True,
                schedule_local_shutdown=lambda: False,
                schedule_stack_shutdown=lambda: False,
                schedule_bot_shutdown=lambda: scheduled.append("bot"),
            ),
            "종료 helper 실행에 실패해서 bot process만 정리할게.",
        )
        self.assertEqual(scheduled, ["bot"])
        self.assertEqual(
            build_control_page_minecraft_connect_reply_payload(
                {"objective_goal": "diamond", "objective_stage": "mine"},
                position_text="1, 2, 3",
            ),
            "Voyager Minecraft 모드를 시작했어.\n- goal: diamond\n- stage: mine\n- position: 1, 2, 3",
        )
        self.assertEqual(build_control_page_minecraft_disconnect_reply(), "Voyager Minecraft 모드를 중지했어.")
        self.assertEqual(
            build_control_page_minecraft_goal_missing_reply(),
            "목표를 같이 적어줘. 예: /minecraft goal progress_to_diamond",
        )
        self.assertEqual(
            build_control_page_minecraft_goal_updated_reply("diamond", {"stage": "mine"}),
            "Minecraft 목표를 바꿨어.\n- goal: diamond\n- stage: mine",
        )


if __name__ == "__main__":
    unittest.main()
