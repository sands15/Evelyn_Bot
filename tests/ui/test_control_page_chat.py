from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
CONTROL_PAGE = REPO_ROOT / "docs" / "index.html"
CONTROL_BOOT_PROGRESS_JS = REPO_ROOT / "docs" / "assets" / "evelyn-boot-progress.js"
CONTROL_PAGE_CSS = REPO_ROOT / "docs" / "assets" / "evelyn-page.css"


class ControlPageChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = CONTROL_PAGE.read_text(encoding="utf-8")
        cls.boot_js = CONTROL_BOOT_PROGRESS_JS.read_text(encoding="utf-8")
        cls.css = CONTROL_PAGE_CSS.read_text(encoding="utf-8")

    def test_chat_log_renders_state_messages(self) -> None:
        self.assertIn('id="chatLog"', self.html)
        self.assertIn("function renderChatMessages(messages)", self.html)
        self.assertIn("renderChatMessages(state.chat.messages)", self.html)
        self.assertIn("const STATE_POLL_MS = 1500;", self.html)
        self.assertIn("let statePollTimer = null;", self.html)
        self.assertIn("let initialStateLoaded = false;", self.html)
        self.assertIn("function scheduleStatePolling()", self.html)
        self.assertIn("refreshState({ runInitialPanelCommands: false, showConnectionErrors: false })", self.html)
        self.assertIn("if (bootProgressReady(payload))", self.html)
        self.assertIn("scheduleStatePolling();", self.html)
        self.assertIn("applyState(payload, { runInitialPanelCommands });", self.html)
        self.assertIn("showConnectionErrors || !initialStateLoaded", self.html)

    def test_chat_polling_shows_new_message_indicator_when_scrolled_up(self) -> None:
        self.assertIn("id=\"chatNewMessageRow\"", self.html)
        self.assertIn("id=\"chatNewMessageButton\"", self.html)
        self.assertIn("새 메시지", self.html)
        self.assertIn("class=\"chat-new-message-row\"", self.html)
        self.assertIn("chatLog.addEventListener(\"scroll\"", self.html)
        self.assertIn("function showNewChatMessageNotice()", self.html)
        self.assertIn("function hideNewChatMessageNotice()", self.html)
        self.assertIn("if (previousCount > 0 && hasNewMessages) {", self.html)
        self.assertIn("if (isChatScrolledNearBottom())", self.html)
        self.assertIn("function chatSignature(messages)", self.html)
        self.assertIn("showNewChatMessageNotice();", self.html)
        self.assertIn("chatNewMessageRow", self.html)

    def test_send_uses_conversation_log_instead_of_single_bubble_only(self) -> None:
        self.assertIn("appendChatMessage({", self.html)
        self.assertNotIn('lastBubble.querySelector(".caption").textContent = text;', self.html)

    def test_mutating_requests_use_control_page_csrf_session(self) -> None:
        self.assertIn('"/api/control-page/session"', self.html)
        self.assertIn('"X-Evelyn-CSRF-Token"', self.html)
        self.assertIn('"Content-Type"] =', self.html)
        self.assertIn('response.status === 403 && mutating', self.html)

    def test_user_display_name_is_not_mojibake(self) -> None:
        self.assertIn('author: "정훈"', self.html)
        self.assertIn("function normalizeChatAuthor(author, role)", self.html)
        self.assertIn("normalizeChatAuthor(message.author || fallbackAuthor, normalizedRole)", self.html)
        self.assertNotIn('?뺥썕", text', self.html)

    def test_control_page_asset_javascript_parses(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        boot_result = subprocess.run(
            [node, "--check", str(CONTROL_BOOT_PROGRESS_JS)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(boot_result.returncode, 0, boot_result.stderr + boot_result.stdout)
        inline_script = self.html.split("<script>", 1)[1].split("</script>", 1)[0]
        result = subprocess.run(
            [node, "--check", "-"],
            cwd=REPO_ROOT,
            input=inline_script,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_initial_static_bubble_does_not_explain_memory_command(self) -> None:
        self.assertNotIn("?? ?ш린 ?덉뼱. 硫붾え由щ뒗 /memory ?쇨퀬 ?낅젰?섎㈃ ?댁뼱?섍쾶.", self.html)
        self.assertIn('<span class="caption">...</span>', self.html)

    def test_chat_surface_keeps_transparent_bubbles_and_themed_scrollbars(self) -> None:
        self.assertIn("--chat-panel: transparent;", self.html)
        self.assertIn("--chat-panel-user: transparent;", self.html)
        self.assertIn("--chat-input:", self.html)
        self.assertIn("--scrollbar-track: transparent;", self.html)
        self.assertIn("--scrollbar-thumb:", self.html)
        self.assertIn("--stage-bg: var(--bg);", self.html)
        self.assertIn("width: min(520px, 42vw);", self.html)
        self.assertIn("padding-right: 4px;", self.html)
        self.assertNotIn("background: var(--chat-log-bg);", self.html)
        self.assertIn("background: var(--chat-panel);", self.html)
        self.assertIn("box-shadow: none;", self.html)
        self.assertIn("backdrop-filter: none;", self.html)
        self.assertIn("background: var(--chat-input);", self.html)
        self.assertIn("scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track);", self.html)
        self.assertIn(".chat-log::-webkit-scrollbar-thumb", self.html)

    def test_secondary_scroll_surfaces_are_themed(self) -> None:
        self.assertIn(".drawer::-webkit-scrollbar-thumb", self.html)
        self.assertIn(".memory-cards::-webkit-scrollbar-thumb", self.html)
        self.assertIn("body::-webkit-scrollbar-thumb", self.html)
        self.assertIn(".left-rail::-webkit-scrollbar-thumb", self.css)
        self.assertIn("#minecraft-telemetry-panel::-webkit-scrollbar-thumb", self.css)
        self.assertIn(".inventory-widget::-webkit-scrollbar-thumb", self.css)
        self.assertIn(".memory-card-list::-webkit-scrollbar-thumb", self.css)
        self.assertIn(".memory-graph-detail::-webkit-scrollbar-thumb", self.css)

    def test_chat_log_can_grow_toward_page_top(self) -> None:
        self.assertIn("max-height: min(620px, calc(100vh - 214px));", self.html)
        self.assertIn("max-height: calc(100svh - 274px);", self.html)
        self.assertNotIn("max-height: min(300px, 34vh);", self.html)

    def test_natural_memory_panel_commands_go_through_backend_tool_router(self) -> None:
        self.assertNotIn("function memoryPanelActionFromText(value)", self.html)
        self.assertNotIn("const memoryPanelAction = memoryPanelActionFromText(value);", self.html)
        self.assertNotIn('applyControlPanelCommand({ action: memoryPanelAction, panel: "memory" });', self.html)
        self.assertIn('fetchApi("/api/control-page/chat"', self.html)

    def test_fast_command_catalog_only_advertises_wired_voice_commands(self) -> None:
        for command in (
            "/help",
            "/status",
            "/remember <fact>",
            "/memory",
            "/obsidian",
            "/voice status",
            "/mic status",
            "/mic on",
            "/mic off",
            "/minecraft connect",
            "/minecraft status",
            "/inventory",
            "/voyager stats",
            "/minecraft disconnect",
            "/minecraft goal <goal>",
            "/autonomy status",
            "/repair preview",
            "/repair start",
            "/restart",
            "/shutdown",
        ):
            with self.subTest(command=command):
                self.assertIn(f'{{ command: "{command}"', self.html)

    def test_chat_assigns_a_stable_request_id_for_memory_evidence(self) -> None:
        self.assertIn("globalThis.crypto.randomUUID()", self.html)
        self.assertIn("requestId:", self.html)
        self.assertNotIn('{ command: "/voice continuity"', self.html)
        self.assertNotIn('{ command: "/voice input auto"', self.html)

    def test_chat_retry_id_expires_before_server_ingress_lease(self) -> None:
        self.assertIn(
            'const PENDING_CHAT_STORAGE_KEY = "evelyn.control-page.pending-chat.v1";',
            self.html,
        )
        self.assertIn(
            "const PENDING_CHAT_MAX_AGE_MS = 14 * 60 * 1000;",
            self.html,
        )
        self.assertIn(
            "below the server's 15-minute ingress lease",
            self.html,
        )
        self.assertIn("function pendingChatMessageFor(text)", self.html)
        self.assertIn("existing && existing.text === text", self.html)
        self.assertIn("Date.now() - createdAt > PENDING_CHAT_MAX_AGE_MS", self.html)

    def test_chat_clears_retry_id_only_after_confirmed_success(self) -> None:
        success_guard = 'if (!payload || payload.ok !== true) {'
        clear_call = "clearPendingChatMessage(pendingChat.requestId);"
        self.assertIn(success_guard, self.html)
        self.assertIn('throw new Error("chat_delivery_unconfirmed")', self.html)
        self.assertLess(
            self.html.index(success_guard),
            self.html.index(clear_call),
        )
        self.assertIn("if (!composer.value.trim()) composer.value = value;", self.html)

    def test_control_panel_commands_drive_memory_window(self) -> None:
        self.assertIn("let lastControlPanelCommandId = 0;", self.html)
        self.assertIn('let controlPanelCommandGeneration = "";', self.html)
        self.assertIn("function applyControlPanelCommands(state, options = {})", self.html)
        self.assertIn(
            "generation !== controlPanelCommandGeneration",
            self.html,
        )
        self.assertIn("lastControlPanelCommandId = 0;", self.html)
        self.assertIn("runInitialPanelCommands", self.html)
        self.assertIn("applyState(payload.state, { runInitialPanelCommands: true });", self.html)
        self.assertIn('String(command.panel || "") !== "memory"', self.html)
        self.assertIn('action === "open"', self.html)
        self.assertIn("toggleMemoryWindow(true, options);", self.html)
        self.assertIn('action === "close"', self.html)
        self.assertIn("toggleMemoryWindow(false, options);", self.html)

    def test_panel_command_generation_resets_restart_cursor(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        command_start = self.html.index("    function applyControlPanelCommand")
        command_end = self.html.index("\n    function apiCandidates", command_start)
        function_source = self.html[command_start:command_end]
        script = "\n".join(
            (
                "const events = [];",
                "let lastControlPanelCommandId = 5;",
                "let controlPanelCommandCursorReady = true;",
                'let controlPanelCommandGeneration = "old";',
                "const drawer = {classList: {add(name) { events.push(name); }}};",
                "const document = {getElementById(id) {",
                "  if (id !== 'voiceValidationStartButton') return null;",
                "  return {",
                "    scrollIntoView() { events.push('scroll'); },",
                "    focus() { events.push('focus'); },",
                "  };",
                "}};",
                "function toggleMemoryWindow() { process.exit(2); }",
                function_source,
                "const restarted = {controlPagePanels: {generation: 'new', commands: [",
                "  {id: 1, panel: 'voice_validation', action: 'open'}",
                "]}};",
                "applyControlPanelCommands(restarted);",
                "applyControlPanelCommands(restarted);",
                "if (JSON.stringify(events) !== JSON.stringify(['open', 'scroll', 'focus'])) {",
                "  process.exit(1);",
                "}",
            )
        )
        result = subprocess.run(
            [node, "-e", script],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_boot_splash_markup_is_present(self) -> None:
        self.assertIn('class="is-boot-splash-active"', self.html)
        self.assertIn('id="boot-splash"', self.html)
        self.assertIn('id="boot-splash-phase"', self.html)
        self.assertIn('id="boot-splash-bar"', self.html)
        self.assertIn('id="boot-splash-shutdown"', self.html)

    def test_wallpaper_picker_has_default_restore_button(self) -> None:
        self.assertIn('id="wallpaperResetButton"', self.html)
        self.assertIn("app.style.removeProperty(\"--custom-wallpaper\");", self.html)
        self.assertIn("localStorage.removeItem(\"evelynControlWallpaper\");", self.html)

    def test_wallpaper_uses_persistent_blob_storage_and_validates_images(self) -> None:
        self.assertIn('id="wallpaperPicker"', self.html)
        self.assertIn('const WALLPAPER_DB_NAME = "evelynControlPage"', self.html)
        self.assertIn('const WALLPAPER_STORE_KEY = "wallpaper"', self.html)
        self.assertIn("function openWallpaperDatabase()", self.html)
        self.assertIn("async function storeWallpaper(file)", self.html)
        self.assertIn("async function restoreWallpaper()", self.html)
        self.assertIn("async function applyWallpaperBlob(blob)", self.html)
        self.assertIn("await decodedWallpaperUrl(blob)", self.html)
        self.assertIn("URL.createObjectURL(blob)", self.html)
        self.assertIn("await storeWallpaper(file)", self.html)
        self.assertIn("await deleteStoredWallpaper()", self.html)

    def test_boot_splash_hides_only_when_components_are_ready(self) -> None:
        self.assertIn('<script src="./assets/evelyn-boot-progress.js"></script>', self.html)
        self.assertIn("window.EvelynBootProgress =", self.boot_js)
        self.assertIn("function hasReadyRuntimeServices(payload)", self.boot_js)
        self.assertIn("services.mainReady", self.boot_js)
        self.assertIn("services.routerReady", self.boot_js)
        self.assertIn("services.subReady", self.boot_js)
        self.assertIn("services.ttsReady", self.boot_js)
        self.assertIn('"sttReady", "STT"', self.boot_js)
        self.assertIn("function progressFromPayload(payload)", self.boot_js)
        self.assertIn("function isReady(payload)", self.boot_js)
        self.assertIn("function applyBootProgressPayload(payload)", self.html)
        self.assertIn("function hasReadyRuntimeServices(payload)", self.html)
        self.assertIn("applyBootProgressPayload(state);", self.html)
        self.assertIn("return window.EvelynBootProgress.hasReadyRuntimeServices(payload);", self.html)
        self.assertIn("return window.EvelynBootProgress.fromRuntimeServices(payload);", self.html)
        self.assertIn("return window.EvelynBootProgress.isReady(payload);", self.html)
        self.assertIn("const progress = window.EvelynBootProgress.progressFromPayload(payload);", self.html)
        self.assertIn("function bootProgressFromRuntimeServices(payload)", self.html)
        self.assertIn("const BOOT_PROGRESS_POLL_MS = 1200;", self.html)
        self.assertIn("let bootProgressPollTimer = null;", self.html)
        self.assertIn("function bootProgressReady(payload)", self.html)
        self.assertIn("function scheduleBootProgressPolling(payload)", self.html)
        self.assertIn("scheduleBootProgressPolling(payload);", self.html)
        self.assertIn("scheduleBootProgressPolling(null);", self.html)
        self.assertIn("bootProgressPollTimer = window.setTimeout(() =>", self.html)
        self.assertNotIn("const apiConnected = payload.ok !== false;", self.html)
        self.assertNotIn("hide: apiConnected", self.html)

    def test_ready_chat_survives_transient_control_page_degradation(self) -> None:
        self.assertIn("let hasReachedReadyState = false;", self.html)
        self.assertIn("const showSplash = visible && !hasReachedReadyState;", self.html)
        self.assertIn("const continuity = window.EvelynBootProgress.continuityDecision", self.html)
        self.assertIn("hasReachedReadyState = continuity.reached;", self.html)
        self.assertIn("if (hasReachedReadyState || bootProgressReady(payload)) return;", self.html)
        self.assertIn("function shouldPreserveChatHistory(state)", self.html)
        self.assertIn(".continuityDecision(hasReachedReadyState, state)", self.html)
        self.assertIn(
            "if (!preserveChatHistory && state.chat && Array.isArray(state.chat.messages))",
            self.html,
        )
        self.assertIn("const chatHistoryPreserved = applyState(payload.state", self.html)
        self.assertIn("if (chatHistoryPreserved && payload.reply) setBubble(payload.reply);", self.html)

    def test_state_poll_is_single_flight_and_stale_responses_are_ignored(self) -> None:
        self.assertIn("let statePollInFlight = false;", self.html)
        self.assertIn("let stateRefreshGeneration = 0;", self.html)
        self.assertIn("if (!sending && !statePollInFlight)", self.html)
        self.assertIn("statePollInFlight = true;", self.html)
        self.assertIn("statePollInFlight = false;", self.html)
        self.assertIn("const refreshGeneration = ++stateRefreshGeneration;", self.html)
        self.assertNotIn("applyState(state);", self.html)
        self.assertGreaterEqual(self.html.count("shouldApplyStateResponse("), 2)
        self.assertIn("stateRefreshGeneration += 1;\n      sending = true;", self.html)
        self.assertIn("stateRefreshGeneration += 1;\n        sending = false;", self.html)

    def test_busy_memory_state_keeps_last_ui_and_retries(self) -> None:
        fetch_contract = self.html.split(
            "async function fetchApi", 1
        )[1].split("function shouldPreserveChatHistory", 1)[0]
        self.assertIn(
            'evelynErrorCode === "memory_deletion_journal_busy"',
            fetch_contract,
        )
        self.assertIn(
            'apiError.evelynErrorCode = evelynErrorCode;',
            fetch_contract,
        )
        refresh_contract = self.html.split(
            "async function refreshState", 1
        )[1].split("function renderSuggestions", 1)[0]
        busy_branch = refresh_contract.index(
            'error.evelynErrorCode || "") === "memory_deletion_journal_busy"'
        )
        generic_failure = refresh_contract.index("setBootProgress(0,")
        self.assertLess(busy_branch, generic_failure)
        self.assertIn(
            "scheduleBootProgressPolling(null);\n          return;",
            refresh_contract[busy_branch:generic_failure],
        )

    def test_chat_continuity_transition_table_executes(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        script = f"""
global.window = {{}};
require({json.dumps(str(CONTROL_BOOT_PROGRESS_JS))});
const contract = window.EvelynBootProgress;
const degraded = {{
  ok: false,
  ui: {{ reason: "bot_api_proxy_pending" }},
  bootProgress: {{ percent: 100, ready: true, componentsReady: true }}
}};
const healthy = {{
  ok: true,
  ui: {{ reason: "docker_fast_control" }},
  bootProgress: {{ percent: 100, ready: true, componentsReady: true }}
}};
function check(value, message) {{ if (!value) throw new Error(message); }}
let decision = contract.continuityDecision(false, degraded);
check(!decision.ready && !decision.reached && !decision.preserveChat, "fresh degraded");
decision = contract.continuityDecision(decision.reached, healthy);
check(decision.ready && decision.reached && !decision.preserveChat, "first ready");
decision = contract.continuityDecision(decision.reached, degraded);
check(!decision.ready && decision.reached && decision.preserveChat, "ready then degraded");
decision = contract.continuityDecision(decision.reached, healthy);
check(decision.ready && decision.reached && !decision.preserveChat, "recovered");
check(!contract.shouldApplyStateResponse(1, 2, false), "stale generation");
check(!contract.shouldApplyStateResponse(2, 2, true), "send in flight");
check(contract.shouldApplyStateResponse(2, 2, false), "current response");
"""
        result = subprocess.run(
            [node, "-e", script],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_runtime_service_health_is_rendered_without_replacing_legacy_services(self) -> None:
        self.assertIn('id="runtimeHealthLine"', self.html)
        self.assertIn("function runtimeHealthText(payload)", self.html)
        self.assertIn("function runtimeHealthObject(payload)", self.html)
        self.assertIn("runtime.serviceHealth || payload.serviceHealth", self.html)
        self.assertIn("runtimeHealthLine.textContent = runtimeHealthText(state);", self.html)

    def test_discord_mode_toggle_waits_for_service_health_confirmation(self) -> None:
        self.assertIn('id="discordModeLine"', self.html)
        self.assertIn('id="discordModeToggleButton"', self.html)
        self.assertIn('service.id === "discord_bot"', self.html)
        self.assertIn("function renderDiscordMode(state)", self.html)
        self.assertIn("renderDiscordMode(state);", self.html)
        self.assertIn("discordModeTransitionTarget", self.html)
        self.assertIn('fetchApi("/api/control-page/discord-mode/preview"', self.html)
        self.assertIn('fetchApi("/api/control-page/discord-mode/apply"', self.html)
        self.assertIn("preview.confirmToken", self.html)
        self.assertIn("window.confirm", self.html)
        self.assertIn("Control Page는 계속 실행돼", self.html)

    def test_runtime_health_diagnosis_map_covers_required_codes(self) -> None:
        for code in (
            "CP_UP_BOT_DOWN",
            "BOT_API_DOWN_WITH_CONTROL_PAGE_UP",
            "BOT_API_PARTIAL",
            "CONTROL_PAGE_DOWN",
            "MAIN_LLM_DOWN",
            "ROUTER_LLM_DOWN",
            "SUB_LLM_DOWN",
            "TTS_DOWN",
            "VISION_DOWN",
            "VOYAGER_DOWN",
            "CODEX_GATEWAY_DOWN",
            "CODEX_GATEWAY_ACTION_FAILED",
            "VOYAGER_TASK_CONTRACT_UNVERIFIED",
            "VOYAGER_TASK_CONTRACT_FAILED",
            "VOYAGER_TASK_RECOVERY_REQUIRED",
            "VOYAGER_RUNTIME_RECOVERY_REQUIRED",
        ):
            self.assertIn(f"{code}:", self.html)
        self.assertIn("runtimeHealthCodeTextFromCode(primary.code)", self.html)

    def test_runtime_health_messages_are_readable_and_prioritized(self) -> None:
        for message in (
            "Control-Page is up, but Bot API is down.",
            "Control-Page is open, but Bot API is not responding.",
            "Bot API is partially responding or failing some requests.",
            "Control-Page server is not responding.",
            "Main LLM is not responding.",
            "Router LLM is not responding.",
            "Sub LLM is not responding.",
            "TTS is not responding.",
            "Vision is not responding.",
            "Voyager is not responding.",
            "Codex Gateway is not responding.",
            "Codex Gateway action execution failed.",
            "Voyager task contract is unverified.",
            "Voyager task contract failed.",
            "Voyager task recovery is required.",
            "Voyager runtime recovery is required.",
        ):
            self.assertIn(message, self.html)

    def test_contract_timeline_renders_voyager_contract_diagnostics(self) -> None:
        self.assertIn('id="contractTimelineStatus"', self.html)
        self.assertIn('id="contractTimelineList"', self.html)
        self.assertIn("Voyager 계약 추적 타임라인", self.html)
        self.assertIn(".contract-timeline", self.html)
        self.assertIn("function contractTimelineDiagnostics(payload)", self.html)
        self.assertIn("function renderContractTimeline(payload)", self.html)
        self.assertIn("renderContractTimeline(state);", self.html)
        self.assertIn('code.startsWith("VOYAGER_TASK_CONTRACT")', self.html)
        self.assertIn('code === "VOYAGER_RUNTIME_RECOVERY_REQUIRED"', self.html)
        self.assertIn("contractTimelineList.innerHTML", self.html)

    def test_runtime_status_summary_uses_active_health_payload(self) -> None:
        self.assertIn("const runtime = (payload && payload.runtime) || {};", self.html)
        self.assertIn("const health = runtime.serviceHealth || payload.serviceHealth || null;", self.html)
        self.assertIn("const diagnostics = Array.isArray(health.diagnostics) ? health.diagnostics : [];", self.html)
        self.assertIn("return String(health.summary || health.overallState || \"ready\");", self.html)

    def test_runtime_repair_action_text_uses_readable_copy(self) -> None:
        for label in (
            "Preview Bot API restart",
            "Preview Bot API health repair",
            "Preview Control-Page restart",
            "Preview Main LLM repair",
            "Preview Router LLM repair",
            "Preview Sub LLM repair",
            "Preview TTS repair",
            "Preview Voyager repair",
            "Preview Codex Gateway repair",
        ):
            self.assertIn(label, self.html)
        self.assertIn("is-repair-preview", self.html)
        self.assertIn("async function requestRuntimeRepairPreview()", self.html)
        self.assertIn("async function requestRuntimeRepairApply()", self.html)

    def test_runtime_repair_preview_status_copy_is_readable(self) -> None:
        self.assertIn("return `Ready to start: ${commandText}`;", self.html)
        self.assertIn("return `Preview failed: ${message}`;", self.html)
        self.assertIn('button.textContent = "Preview checked"', self.html)
        self.assertIn('button.textContent = "Preview failed"', self.html)
        self.assertIn("if (!window.confirm(`Start ${action.serviceId} repair launcher?`))", self.html)
        self.assertIn('setRuntimeRepairPreviewStatus("Starting repair launcher...")', self.html)
        self.assertIn("setRuntimeRepairPreviewStatus(`Repair start failed: ${error.message}`)", self.html)

    def test_runtime_repair_preview_uses_dry_run_ui_button(self) -> None:
        self.assertIn(".quick-command.is-repair-preview", self.css)
        self.assertIn(".quick-command.is-repair-apply", self.css)
        self.assertIn('id="quick-command-row"', self.html)
        self.assertIn('id="quick-command-caption"', self.html)
        self.assertIn('id="runtimeRepairPreviewButton"', self.html)
        self.assertIn("function runtimeRepairActionFromLegacyState(state)", self.html)
        self.assertIn("runtimeRepairActionFromLegacyState(payload)", self.html)
        self.assertIn("function runtimeDiagnosticIsConfirmedFailure(diagnostic, state)", self.html)
        self.assertIn("runtimeRepairReadyServices.has(serviceId)", self.html)
        self.assertIn("RUNTIME_REPAIR_STARTUP_GRACE_MS", self.html)
        self.assertIn("suggested.actionId || suggested.action_id || suggested.id", self.html)
        self.assertIn("`start_${serviceId}`", self.html)
        self.assertIn("function requestRuntimeRepairPreview()", self.html)
        self.assertIn("function requestRuntimeRepairApply()", self.html)
        self.assertIn('fetchApi("/api/control-page/runtime-repair/preview"', self.html)
        self.assertIn('fetchApi("/api/control-page/runtime-repair/apply"', self.html)
        self.assertIn("JSON.stringify({ actionId, serviceId, dryRun: true })", self.html)
        self.assertIn("confirmToken", self.html)
        self.assertIn("window.confirm", self.html)
        self.assertIn("renderRuntimeRepairPreview(state);", self.html)
        self.assertIn(".quick-command.is-repair-preview", self.html)
        self.assertIn(".quick-command.is-repair-apply", self.html)


if __name__ == "__main__":
    unittest.main()
