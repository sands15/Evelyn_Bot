from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
CONTROL_PAGE = REPO_ROOT / "docs" / "index.html"
CONTROL_BOOT_PROGRESS_JS = REPO_ROOT / "docs" / "assets" / "evelyn-boot-progress.js"
CONTROL_PAGE_JS = REPO_ROOT / "docs" / "assets" / "evelyn-page.js"
CONTROL_PAGE_CSS = REPO_ROOT / "docs" / "assets" / "evelyn-page.css"


class ControlPageChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = CONTROL_PAGE.read_text(encoding="utf-8")
        cls.boot_js = CONTROL_BOOT_PROGRESS_JS.read_text(encoding="utf-8")
        cls.js = CONTROL_PAGE_JS.read_text(encoding="utf-8")
        cls.css = CONTROL_PAGE_CSS.read_text(encoding="utf-8")

    def test_chat_log_renders_state_messages(self) -> None:
        self.assertIn('id="chatLog"', self.html)
        self.assertIn("function renderChatMessages(messages)", self.html)
        self.assertIn("renderChatMessages(state.chat.messages)", self.html)
        self.assertIn("const STATE_POLL_MS = 1500;", self.html)
        self.assertIn("let statePollTimer = null;", self.html)
        self.assertIn("let initialStateLoaded = false;", self.html)
        self.assertIn("function scheduleStatePolling()", self.html)
        self.assertIn("refreshState({ runInitialPanelCommands: false, showConnectionErrors: false });", self.html)
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
        self.assertIn("chatSignature(rows)", self.js)
        self.assertIn("showNewChatMessageNotice()", self.js)
        self.assertIn("dom.chatNewMessageRow", self.js)

    def test_send_uses_conversation_log_instead_of_single_bubble_only(self) -> None:
        self.assertIn("appendChatMessage({", self.html)
        self.assertNotIn('lastBubble.querySelector(".caption").textContent = text;', self.html)

    def test_user_display_name_is_not_mojibake(self) -> None:
        self.assertIn('author: "정훈"', self.html)
        self.assertIn('"정훈"', self.js)
        self.assertIn("function normalizeChatAuthor(author, role)", self.html)
        self.assertIn("function normalizeDisplayAuthor(author, role)", self.js)
        self.assertIn("normalizeChatAuthor(message.author || fallbackAuthor, normalizedRole)", self.html)
        self.assertIn("normalizeDisplayAuthor(row.author, role)", self.js)
        self.assertNotIn('?뺥썕", text', self.html)
        self.assertNotIn('?뺥썕", text', self.js)

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
        result = subprocess.run(
            [node, "--check", str(CONTROL_PAGE_JS)],
            cwd=REPO_ROOT,
            text=True,
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

    def test_control_panel_commands_drive_memory_window(self) -> None:
        self.assertIn("let lastControlPanelCommandId = 0;", self.html)
        self.assertIn("function applyControlPanelCommands(state, options = {})", self.html)
        self.assertIn("runInitialPanelCommands", self.html)
        self.assertIn("applyState(payload.state, { runInitialPanelCommands: true });", self.html)
        self.assertIn('String(command.panel || "") !== "memory"', self.html)
        self.assertIn('action === "open"', self.html)
        self.assertIn("toggleMemoryWindow(true, options);", self.html)
        self.assertIn('action === "close"', self.html)
        self.assertIn("toggleMemoryWindow(false, options);", self.html)

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
        self.assertIn("function hasReadyRuntimeServices(payload)", self.js)
        self.assertIn("return window.EvelynBootProgress.hasReadyRuntimeServices(payload);", self.js)
        self.assertIn("return window.EvelynBootProgress.fromRuntimeServices(payload);", self.js)
        self.assertIn("return window.EvelynBootProgress.isReady(payload);", self.js)
        self.assertIn("const progress = window.EvelynBootProgress.progressFromPayload(payload);", self.js)
        self.assertIn("function bootProgressFromRuntimeServices(payload)", self.js)
        self.assertIn("function shouldRevealControlSurfaceDuringBoot(payload)", self.js)
        self.assertIn("shouldRevealControlSurfaceDuringBoot(payload)", self.js)
        self.assertIn('setApiBootProgress(componentsReady ? 100 : percent, componentsReady ? "Control Ready" : phase, { hide: componentsReady });', self.js)
        self.assertNotIn("const apiConnected = payload.ok !== false;", self.js)
        self.assertNotIn("hide: apiConnected", self.js)

    def test_runtime_service_health_is_rendered_without_replacing_legacy_services(self) -> None:
        self.assertIn("function runtimeHealthFromPayload(payload)", self.js)
        self.assertIn("function runtimeHealthIssueText(health)", self.js)
        self.assertIn("const serviceHealth = runtimeHealthFromPayload(payload);", self.js)
        self.assertIn("const runtimeIssue = runtimeHealthHasIssue(serviceHealth);", self.js)
        self.assertIn("runtime.serviceHealth || payload.serviceHealth", self.js)
        self.assertIn("runtimeIssueDetail || services.codexError || services.voyagerError", self.js)
        self.assertIn('id="runtimeHealthLine"', self.html)
        self.assertIn("function runtimeHealthText(payload)", self.html)
        self.assertIn("runtimeHealthLine.textContent = runtimeHealthText(state);", self.html)

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
        ):
            self.assertIn(f"{code}:", self.js)
        self.assertIn("runtimeHealthCodeText(health)", self.js)

    def test_runtime_health_messages_are_readable_and_prioritized(self) -> None:
        self.assertIn("Control-Page is up, but Bot API is down.", self.js)
        self.assertIn("Control-Page is open, but Bot API is not responding.", self.js)
        self.assertIn("Bot API is partially responding or failing some requests.", self.js)
        self.assertIn("Control-Page server is not responding.", self.js)
        self.assertIn("Main LLM is not responding.", self.js)
        self.assertIn("Router LLM is not responding.", self.js)
        self.assertIn("Sub LLM is not responding.", self.js)
        self.assertIn("TTS is not responding.", self.js)

    def test_runtime_status_summary_uses_readable_text(self) -> None:
        self.assertIn('issues.push("Runtime: " + runtimeIssue);', self.js)
        self.assertIn('cleanDisplayText(runtimeIssueText, "Check runtime state.")', self.js)
        self.assertIn('cleanDisplayText(runtimeIssueDetail, "Check runtime diagnosis.")', self.js)
        self.assertIn('dom.operatorRuntimeTitle.textContent = runtimeIssue', self.js)
        self.assertIn("const controlPlane = runtime.controlPlane || {};", self.js)
        self.assertIn("runtimeIssueText || controlPlane.statusText || payload.statusText", self.js)

    def test_control_page_asset_has_readable_fallback_copy(self) -> None:
        self.assertIn("No inventory snapshot yet.", self.js)
        self.assertIn('brief.issueTitle || "Attention"', self.js)
        self.assertIn("No issue details available.", self.js)
        self.assertIn("No recent activity yet.", self.js)
        self.assertIn('aria-label="Reset node scale to 1.00x">\' + escapeHtml(scale.toFixed(2)) + "x</button>"', self.js)
        self.assertIn('text.includes("Evelyn status")', self.js)
        for broken in (
            "?쒖떆",
            "?뱀씠",
            "?꾩쭅",
            "數?",
            "Evelyn ?곹깭",
            "二쇱쓽",
        ):
            self.assertNotIn(broken, self.js)

    def test_runtime_repair_action_text_uses_readable_copy(self) -> None:
        self.assertIn('repairActionLabel: "Preview Bot API restart"', self.js)
        self.assertIn('repairActionLabel: "Preview Bot API health repair"', self.js)
        self.assertIn('repairActionLabel: "Preview Control-Page restart"', self.js)
        self.assertIn('repairActionLabel: "Preview Main LLM repair"', self.js)
        self.assertIn('repairActionLabel: "Preview Router LLM repair"', self.js)
        self.assertIn('repairActionLabel: "Preview Sub LLM repair"', self.js)
        self.assertIn('repairActionLabel: "Preview TTS repair"', self.js)
        self.assertIn('main_llm: "Preview Main LLM repair"', self.js)
        self.assertIn("is-repair-preview", self.js)
        self.assertIn("requestRuntimeRepairPreview(button)", self.js)
        self.assertIn("requestRuntimeRepairApply(button)", self.js)

    def test_runtime_repair_preview_status_copy_is_readable(self) -> None:
        self.assertIn('escapeHtml(item.label || "Preview repair")', self.js)
        self.assertIn('"Ready to start: "', self.js)
        self.assertIn('"Preview only: "', self.js)
        self.assertIn('"Repair preview failed: "', self.js)
        self.assertIn('button.textContent = "Previewing..."', self.js)
        self.assertIn('button.textContent = previousText || "Preview repair"', self.js)
        self.assertIn('button.textContent = "Start: "', self.js)
        self.assertIn('window.confirm("Start repair for " + serviceId + "?")', self.js)
        self.assertIn('button.textContent = "Starting..."', self.js)
        self.assertIn('const text = "Repair failed: " + error.message;', self.js)
        self.assertIn('button.textContent = previousText || "Start repair"', self.js)
        self.assertIn('dom.systemSummaryPill.textContent = ok ? "Ready" : "Preview failed";', self.js)
        self.assertIn('dom.systemSummaryPill.textContent = result.ok ? "Repair started" : "Repair failed";', self.js)

    def test_runtime_repair_action_uses_full_services_priority(self) -> None:
        self.assertIn("const RUNTIME_REPAIR_SERVICE_PRIORITY =", self.js)
        self.assertIn('"main_llm"', self.js)
        self.assertIn('"router_llm"', self.js)
        self.assertIn('"sub_llm"', self.js)
        self.assertIn('"tts"', self.js)
        self.assertIn('"bot_api"', self.js)
        self.assertIn('"control_page"', self.js)
        self.assertIn("function runtimeRepairBlockingServices(health)", self.js)
        self.assertIn("const blockingServices = runtimeRepairBlockingServices(health);", self.js)
        self.assertIn("const preferred = blockingServices[0];", self.js)
        self.assertIn("runtimeHealthSummary:", self.js)
        self.assertIn("runtimeRepairSummaryForBlockingServices(blockingServices)", self.js)
        self.assertIn("recommendedOrder: blockingServices", self.js)

    def test_runtime_repair_action_mentions_main_llm_first_and_follow_up(self) -> None:
        self.assertIn("Preview Main LLM repair", self.js)
        self.assertIn("return `Health check recommends repairing ${firstName} first.`", self.js)
        self.assertIn("Recheck ${followUp[0]} after ${firstName}.", self.js)

    def test_runtime_repair_preview_uses_dry_run_ui_button(self) -> None:
        self.assertIn("function runtimeRepairActionFromPayload(payload)", self.js)
        self.assertIn("repairPreview: true", self.js)
        self.assertIn('data-runtime-repair-preview="1"', self.js)
        self.assertIn("function requestRuntimeRepairPreview(button)", self.js)
        self.assertIn("function requestRuntimeRepairApply(button)", self.js)
        self.assertIn('fetchApi("/api/control-page/runtime-repair/preview"', self.js)
        self.assertIn('fetchApi("/api/control-page/runtime-repair/apply"', self.js)
        self.assertIn("JSON.stringify({ actionId, serviceId, dryRun: true })", self.js)
        self.assertIn("confirmToken", self.js)
        self.assertIn("window.confirm", self.js)
        self.assertIn("function handleQuickActionClick(event)", self.js)
        self.assertIn('event.target.closest("[data-runtime-repair-apply]")', self.js)
        self.assertIn("dom.primaryActionRow.addEventListener(\"click\", handleQuickActionClick)", self.js)
        self.assertIn(".quick-command.is-repair-preview", self.css)
        self.assertIn(".quick-command.is-repair-apply", self.css)
        self.assertIn('id="quick-command-row"', self.html)
        self.assertIn('id="quick-command-caption"', self.html)
        self.assertIn('id="runtimeRepairPreviewButton"', self.html)
        self.assertIn("function runtimeRepairActionFromLegacyState(state)", self.html)
        self.assertIn("runtimeRepairActionFromLegacyState(payload)", self.html)
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
