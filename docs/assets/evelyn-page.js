document.documentElement.classList.remove("no-js");

const revealNodes = document.querySelectorAll(".reveal");
if ("IntersectionObserver" in window) {
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) {
        continue;
      }
      entry.target.classList.add("is-visible");
      observer.unobserve(entry.target);
    }
  }, { threshold: 0.12 });

  for (const node of revealNodes) {
    observer.observe(node);
  }
} else {
  for (const node of revealNodes) {
    node.classList.add("is-visible");
  }
}

const dom = {
  bootSplash: document.querySelector("#boot-splash"),
  bootSplashPhase: document.querySelector("#boot-splash-phase"),
  bootSplashPercent: document.querySelector("#boot-splash-percent"),
  bootSplashTrack: document.querySelector("#boot-splash-track"),
  bootSplashBar: document.querySelector("#boot-splash-bar"),
  bootSplashSteps: document.querySelectorAll("#boot-splash-steps li"),
  bootSplashShutdownButton: document.querySelector("#boot-splash-shutdown"),
  bootSplashShutdownStatus: document.querySelector("#boot-splash-shutdown-status"),
  controlPageRoot: document.querySelector("#control-page-root"),
  controlWallpaperButton: document.querySelector("#control-wallpaper-button"),
  controlWallpaperInput: document.querySelector("#control-wallpaper-input"),
  modelViewport: document.querySelector(".model-viewport"),
  chatThread: document.querySelector("#chat-thread"),
  chatNewMessageRow: document.querySelector("#chat-new-message-row"),
  chatNewMessageButton: document.querySelector("#chat-new-message-button"),
  chatComposer: document.querySelector("#chat-composer"),
  commandInput: document.querySelector("#command-preview"),
  commandSuggestions: document.querySelector("#command-suggestions"),
  quickCommandRow: document.querySelector("#quick-command-row"),
  chatShutdownButton: document.querySelector("#chat-shutdown-button"),
  refreshStateButton: document.querySelector("#refresh-state-button"),
  composerSendButton: document.querySelector("#composer-send-button"),
  ttsTargetName: document.querySelector("#tts-target-name"),
  voicePresencePill: document.querySelector("#voice-presence-pill"),
  operatorRuntimeCard: document.querySelector("#operator-runtime-card"),
  operatorRuntimeTitle: document.querySelector("#operator-runtime-title"),
  operatorRuntimeSubcopy: document.querySelector("#operator-runtime-subcopy"),
  operatorRuntimeDot: document.querySelector("#operator-runtime-dot"),
  operatorRuntimeNote: document.querySelector("#operator-runtime-note"),
  operatorStatChannel: document.querySelector("#operator-stat-channel"),
  operatorStatMode: document.querySelector("#operator-stat-mode"),
  operatorStatTts: document.querySelector("#operator-stat-tts"),
  operatorStatLlm: document.querySelector("#operator-stat-llm"),
  minecraftRuntimeTitle: document.querySelector("#minecraft-runtime-title"),
  minecraftRuntimeSubcopy: document.querySelector("#minecraft-runtime-subcopy"),
  minecraftRuntimeDot: document.querySelector("#minecraft-runtime-dot"),
  minecraftRuntimeStats: document.querySelector("#minecraft-runtime-stats"),
  minecraftIdleNote: document.querySelector("#minecraft-idle-note"),
  minecraftTelemetryPanel: document.querySelector("#minecraft-telemetry-panel"),
  operationsEyebrow: document.querySelector("#operations-eyebrow"),
  operationsTitle: document.querySelector("#operations-title"),
  operationsSubcopy: document.querySelector("#operations-subcopy"),
  minecraftViewportHud: document.querySelector("#minecraft-viewport-hud"),
  statCurrentTask: document.querySelector("#stat-current-task"),
  statStage: document.querySelector("#stat-stage"),
  statUniqueItems: document.querySelector("#stat-unique-items"),
  statTravelDistance: document.querySelector("#stat-travel-distance"),
  statHealthHunger: document.querySelector("#stat-health-hunger"),
  statSkillLibrary: document.querySelector("#stat-skill-library"),
  recentActivityList: document.querySelector("#recent-activity-list"),
  systemSummaryPill: document.querySelector("#system-summary-pill"),
  meterVoyager: document.querySelector("#meter-voyager"),
  meterVoyagerLabel: document.querySelector("#meter-voyager-label"),
  meterVoice: document.querySelector("#meter-voice"),
  meterVoiceLabel: document.querySelector("#meter-voice-label"),
  meterTts: document.querySelector("#meter-tts"),
  meterTtsLabel: document.querySelector("#meter-tts-label"),
  meterLlm: document.querySelector("#meter-llm"),
  meterLlmLabel: document.querySelector("#meter-llm-label"),
  voicePipelineQueue: document.querySelector("#voice-pipeline-queue"),
  voicePipelineStt: document.querySelector("#voice-pipeline-stt"),
  voicePipelineTts: document.querySelector("#voice-pipeline-tts"),
  voicePipelineDrops: document.querySelector("#voice-pipeline-drops"),
  modelCallRouterRate: document.querySelector("#model-call-router-rate"),
  modelCallRouterLatency: document.querySelector("#model-call-router-latency"),
  modelCallMainFirst: document.querySelector("#model-call-main-first"),
  modelCallCognitiveRate: document.querySelector("#model-call-cognitive-rate"),
  modelCallSummaryHot: document.querySelector("#model-call-summary-hot"),
  modelCallTurnCount: document.querySelector("#model-call-turn-count"),
  questionAddedRate: document.querySelector("#question-added-rate"),
  questionRemovedCount: document.querySelector("#question-removed-count"),
  questionCooldownRate: document.querySelector("#question-cooldown-rate"),
  questionAskMode: document.querySelector("#question-ask-mode"),
  questionTurnCount: document.querySelector("#question-turn-count"),
  questionFinalCount: document.querySelector("#question-final-count"),
  voiceInputSwitches: document.querySelectorAll(".voice-input-switch"),
  voiceInputModeButtons: document.querySelectorAll("[data-voice-input-mode]"),
  guildName: document.querySelector("#guild-name"),
  modePill: document.querySelector("#mode-pill"),
  submodePill: document.querySelector("#submode-pill"),
  topbarStatusLine: document.querySelector("#topbar-status-line"),
  apiBootProgress: document.querySelector("#api-boot-progress"),
  apiBootPhase: document.querySelector("#api-boot-phase"),
  apiBootPercent: document.querySelector("#api-boot-percent"),
  apiBootTrack: document.querySelector("#api-boot-track"),
  apiBootBar: document.querySelector("#api-boot-bar"),
  avatarShell: document.querySelector("#avatar-shell"),
  avatarRoot: document.querySelector("#avatar"),
  avatarModel: document.querySelector("#avatar-model"),
  avatarLayerUnderpaint: document.querySelector("#avatar-layer-underpaint"),
  avatarLayerBack: document.querySelector("#avatar-layer-back"),
  avatarLayerLegs: document.querySelector("#avatar-layer-legs"),
  avatarLayerBody: document.querySelector("#avatar-layer-body"),
  avatarLayerTeddy: document.querySelector("#avatar-layer-teddy"),
  avatarLayerHands: document.querySelector("#avatar-layer-hands"),
  avatarLayerFace: document.querySelector("#avatar-layer-face"),
  avatarLayerFeatures: document.querySelector("#avatar-layer-features"),
  avatarLayerFront: document.querySelector("#avatar-layer-front"),
  avatarVolumeShadow: document.querySelector("#avatar-volume-shadow"),
  avatarVolumeLight: document.querySelector("#avatar-volume-light"),
  avatarNeckShadow: document.querySelector("#avatar-neck-shadow"),
  avatarEyeLeft: document.querySelector("#avatar-eye-left"),
  avatarEyeRight: document.querySelector("#avatar-eye-right"),
  avatarMouth: document.querySelector("#avatar-mouth"),
  avatarStatusCopy: document.querySelector("#avatar-status-copy"),
  defaultViewportPanel: document.querySelector("#default-viewport-panel"),
  defaultFocusTitle: document.querySelector("#default-focus-title"),
  defaultFocusBody: document.querySelector("#default-focus-body"),
  defaultFocusRecentTitle: document.querySelector("#default-focus-recent-title"),
  defaultFocusRecentBody: document.querySelector("#default-focus-recent-body"),
  defaultFocusContextTitle: document.querySelector("#default-focus-context-title"),
  defaultFocusContextBody: document.querySelector("#default-focus-context-body"),
  objectiveGoal: document.querySelector("#objective-goal"),
  objectiveProgress: document.querySelector("#objective-progress"),
  objectiveStage: document.querySelector("#objective-stage"),
  objectiveTaskStage: document.querySelector("#objective-task-stage"),
  positionBlock: document.querySelector("#position-block"),
  inventorySummary: document.querySelector("#inventory-summary"),
  inventoryCard: document.querySelector("#inventory-dock"),
  inventoryToggleButton: document.querySelector("#inventory-toggle-button"),
  inventoryWidget: document.querySelector("#inventory-widget"),
  inventoryWidgetClose: document.querySelector("#inventory-widget-close"),
  inventoryWidgetList: document.querySelector("#inventory-widget-list"),
  composerHintLeft: document.querySelector("#composer-hint-left"),
  controlBriefTitle: document.querySelector("#control-brief-title"),
  controlBriefBody: document.querySelector("#control-brief-body"),
  controlNextTitle: document.querySelector("#control-next-title"),
  controlNextBody: document.querySelector("#control-next-body"),
  controlIssueCard: document.querySelector("#control-issue-card"),
  controlIssueTitle: document.querySelector("#control-issue-title"),
  controlIssueBody: document.querySelector("#control-issue-body"),
  quickCommandCaption: document.querySelector("#quick-command-caption"),
  actionsEyebrow: document.querySelector("#actions-eyebrow"),
  actionsSubcopy: document.querySelector("#actions-subcopy"),
  primaryActionTitle: document.querySelector("#primary-action-title"),
  supportActionTitle: document.querySelector("#support-action-title"),
  supportActionCaption: document.querySelector("#support-action-caption"),
  primaryActionRow: document.querySelector("#primary-action-row"),
  minecraftOpsPanel: document.querySelector("#minecraft-ops-panel"),
  minecraftOpsTitle: document.querySelector("#minecraft-ops-title"),
  minecraftOpsBody: document.querySelector("#minecraft-ops-body"),
  minecraftOpsInventoryTitle: document.querySelector("#minecraft-ops-inventory-title"),
  minecraftOpsInventoryBody: document.querySelector("#minecraft-ops-inventory-body"),
  minecraftOpsSurvivalTitle: document.querySelector("#minecraft-ops-survival-title"),
  minecraftOpsSurvivalBody: document.querySelector("#minecraft-ops-survival-body"),
  memoryGraphPanel: document.querySelector("#memory-graph-panel"),
  memoryGraphCanvas: document.querySelector("#memory-graph-canvas"),
  memoryGraphEmpty: document.querySelector("#memory-graph-empty"),
  memoryGraphStats: document.querySelector("#memory-graph-stats"),
  memoryGraphFilter: document.querySelector("#memory-graph-filter"),
  memoryGraphDetail: document.querySelector("#memory-graph-detail"),
  memoryGraphRefreshButton: document.querySelector("#memory-graph-refresh-button"),
  memoryGraphSubcopy: document.querySelector("#memory-graph-subcopy"),
  memoryManagerSummary: document.querySelector("#memory-manager-summary"),
  memoryCardList: document.querySelector("#memory-card-list"),
  memoryManagerStatus: document.querySelector("#memory-manager-status"),
};

const state = {
  apiBase: null,
  commands: [],
  allCommands: [],
  appState: null,
  inputHistory: [],
  historyIndex: -1,
  suggestionItems: [],
  selectedSuggestionIndex: 0,
  sending: false,
  apiWaitStartedAt: 0,
  apiBootProgress: 0,
  renderedChatSignature: "",
  renderedChatMessageCount: 0,
  inventoryWidgetOpen: false,
  panelsReady: false,
  panels: {},
  panelCommandIds: new Set(),
  panelZIndex: 90,
  winboxPanels: {},
  winboxReady: false,
  memoryGraphPayload: null,
  memoryGraphFilterType: "all",
  memoryGraphSelectedNodeId: "",
  memoryGraphLoading: false,
  memoryGraphLastLoadedAt: 0,
  memoryGraphFrame: null,
  memoryGraphNodeScale: Number(localStorage.getItem("evelynMemoryGraphNodeScale") || 1),
  memoryGraphPointer: { x: 0, y: 0, down: false, dragId: "", holdId: "", hoverId: "", offsetX: 0, offsetY: 0, startClientX: 0, startClientY: 0 },
  memoryCardsPayload: null,
  memoryCardsLoading: false,
  memoryCardsLastLoadedAt: 0,
  memoryEditor: null,
  runtimeRepairPreview: null,
  runtimeRepairPreviewBusy: false,
  runtimeRepairApplyBusy: false,
  wallpaperObjectUrl: "",
};

let pollTimer = null;
let pollIntervalMs = 0;
let apiWaitingTicker = null;
const PANEL_LAYOUT_STORAGE_KEY = "evelyn.controlPage.panels.v2";
const PANEL_MANAGER_ENABLED = false;
const WINBOX_PANEL_MANAGER_ENABLED = false;
const WINBOX_LAYOUT_STORAGE_KEY = "evelyn.controlPage.winbox.v3";
const WALLPAPER_DB_NAME = "evelyn-control-page";
const WALLPAPER_DB_VERSION = 1;
const WALLPAPER_STORE_NAME = "assets";
const WALLPAPER_KEY = "wallpaper";
const DEFAULT_OPEN_WINBOX_PANELS = new Set(["avatar", "chat"]);
const PANEL_DEFINITIONS = [
  { id: "runtime", label: "Runtime", selector: ".context-card", handleSelector: ".panel-title-row" },
  { id: "diagnostics", label: "Diagnostics", selector: "#minecraft-telemetry-panel", handleSelector: ".panel-title-row" },
  { id: "avatar", label: "Avatar", selector: ".model-viewport", handleSelector: ".viewport-topbar" },
  { id: "chat", label: "Chat", selector: ".chat-panel", handleSelector: ".chat-header" },
  { id: "memory", label: "Memory", selector: "#memory-graph-panel", handleSelector: ".memory-graph-header" },
];
const WINBOX_RESIZE_CORNERS = ["nw", "ne", "sw", "se"];
const CONTROL_PAGE_COMMAND_CATALOG = [
  { command: "/help", template: "/help", summary: "Show command list" },
  { command: "/status", template: "/status", summary: "Show Evelyn, voice, model, and Minecraft status" },
  { command: "/memory", template: "/memory", summary: "Open or hide the memory panel" },
  { command: "/obsidian", template: "/obsidian", summary: "Open the memory vault" },
  { command: "/voice status", template: "/voice status", summary: "Show voice, STT, and TTS pipeline status" },
  { command: "/voice reconnect", template: "/voice reconnect", summary: "Reconnect to the recent voice channel" },
  { command: "/voice input auto", template: "/voice input auto", summary: "Auto switch local mic and Discord input" },
  { command: "/voice input local", template: "/voice input local", summary: "Use local mic input" },
  { command: "/voice input discord", template: "/voice input discord", summary: "Use Discord voice input" },
  { command: "/minecraft connect", template: "/minecraft connect", summary: "Start Voyager Minecraft mode" },
  { command: "/minecraft status", template: "/minecraft status", summary: "Show Minecraft connection and current task status" },
  { command: "/inventory", template: "/inventory", summary: "Show current Minecraft inventory summary" },
  { command: "/voyager stats", template: "/voyager stats", summary: "Show Voyager progress and metrics" },
  { command: "/minecraft disconnect", template: "/minecraft disconnect", summary: "Stop Voyager Minecraft mode" },
  { command: "/minecraft goal <goal>", template: "/minecraft goal ", summary: "Change Minecraft goal" },
  { command: "/autonomy status", template: "/autonomy status", summary: "Show Evelyn autonomy engine status" },
  { command: "/shutdown", template: "/shutdown", summary: "Shut down Evelyn runtime" },
];
const avatarState = {
  talking: false,
  blinkTimer: null,
  talkTimer: null,
  waveTimer: null,
  rafId: null,
  intensity: 1.85,
  tilt: 1.95,
  targetX: 0,
  targetY: 0,
  currentX: 0,
  currentY: 0,
  velocityX: 0,
  velocityY: 0,
};
const avatarFrames = {
  mouth: {
    idle: "./assets/evelyn-avatar/model-v2/parts/07_mouth_idle.png",
    closed: "./assets/evelyn-avatar/model-v2/parts/07_mouth_idle.png",
    open: "./assets/evelyn-avatar/model-v2/parts/08_mouth_open.png",
    o: "./assets/evelyn-avatar/model-v2/parts/08_mouth_open.png",
  },
  eyes: {
    leftOpen: "./assets/evelyn-avatar/model-v2/parts/05_eye_L.png",
    leftBlink: "./assets/evelyn-avatar/model-v2/parts/05_eye_L.png",
    rightOpen: "./assets/evelyn-avatar/model-v2/parts/06_eye_R.png",
    rightBlink: "./assets/evelyn-avatar/model-v2/parts/06_eye_R.png",
  },
};
const avatarRigLayers = [
  { el: dom.avatarLayerUnderpaint, x: 0.15, y: 0.10, z: -10, rx: 0.02, ry: 0.03, rz: 0.0, scale: 1.003, lag: 0.030, sx: 0, sy: 0, svx: 0, svy: 0 },
  { el: dom.avatarLayerBack, x: 0.35, y: 0.20, z: -6, rx: 0.05, ry: 0.08, rz: 0.02, scale: 1.001, lag: 0.040, sx: 0, sy: 0, svx: 0, svy: 0 },
  { el: dom.avatarLayerLegs, x: 0.45, y: 0.30, z: -2, rx: 0.05, ry: 0.10, rz: 0.02, scale: 1.0005, lag: 0.050, sx: 0, sy: 0, svx: 0, svy: 0 },
  { el: dom.avatarLayerBody, x: 0.70, y: 0.45, z: 0, rx: 0.08, ry: 0.14, rz: 0.03, scale: 1.0008, lag: 0.060, sx: 0, sy: 0, svx: 0, svy: 0 },
  { el: dom.avatarLayerTeddy, x: 0.95, y: 0.55, z: 4, rx: 0.08, ry: 0.12, rz: -0.04, scale: 1.001, lag: 0.055, sx: 0, sy: 0, svx: 0, svy: 0 },
  { el: dom.avatarLayerHands, x: 1.10, y: 0.70, z: 6, rx: 0.10, ry: 0.14, rz: 0.05, scale: 1.0008, lag: 0.075, sx: 0, sy: 0, svx: 0, svy: 0 },
  { el: dom.avatarLayerFace, x: 1.00, y: 0.65, z: 8, rx: 0.06, ry: 0.10, rz: 0.02, scale: 1.0008, lag: 0.070, sx: 0, sy: 0, svx: 0, svy: 0 },
  { el: dom.avatarLayerFeatures, x: 1.15, y: 0.75, z: 10, rx: 0.0, ry: 0.0, rz: 0.0, scale: 1.0, lag: 0.095, sx: 0, sy: 0, svx: 0, svy: 0 },
  { el: dom.avatarLayerFront, x: 1.60, y: 1.00, z: 12, rx: 0.12, ry: 0.18, rz: 0.05, scale: 1.001, lag: 0.080, sx: 0, sy: 0, svx: 0, svy: 0 },
].filter((layer) => layer.el);

const apiWaitingSequence = [
  "Waiting for Evelyn bot response.",
  "Checking the local Control-Page API connection again.",
  "Waking Voyager, voice, and runtime status in order.",
  "Recent status and command buttons will load after connection.",
];

const apiWaitingHints = [
  "Usually connects automatically a few seconds after start.bat runs.",
  "Multiple windows are okay. The page switches when the API responds first.",
  "If opened manually, the default address is http://127.0.0.1:8799/.",
  "During initial boot, model, voice, and Minecraft status attach in sequence.",
];

function clampPercent(value) {
  return window.EvelynBootProgress.clampPercent(value);
}

function setBootSplashVisible(visible) {
  if (!dom.bootSplash) {
    return;
  }
  dom.bootSplash.classList.toggle("is-hidden", !visible);
  dom.bootSplash.setAttribute("aria-hidden", String(!visible));
  document.body.classList.toggle("is-boot-splash-active", visible);
}

function updateBootSplashSteps(percent) {
  if (!dom.bootSplashSteps || dom.bootSplashSteps.length === 0) {
    return;
  }
  let activeIndex = 0;
  dom.bootSplashSteps.forEach((step, index) => {
    const threshold = Number(step.dataset.threshold) || 0;
    const complete = percent >= threshold;
    if (complete) {
      activeIndex = index;
    }
    step.classList.toggle("is-complete", complete);
    step.classList.remove("is-active");
  });
  dom.bootSplashSteps[Math.min(activeIndex, dom.bootSplashSteps.length - 1)]?.classList.add("is-active");
}

function setApiBootProgress(percent, phase, { hide = false } = {}) {
  const nextPercent = clampPercent(percent);
  const nextPhase = phase || "Checking API connection";
  state.apiBootProgress = nextPercent;
  setBootSplashVisible(!hide);
  if (dom.apiBootProgress) {
    dom.apiBootProgress.classList.toggle("is-hidden", Boolean(hide));
    dom.apiBootProgress.setAttribute("aria-hidden", String(Boolean(hide)));
  }
  if (dom.apiBootTrack) {
    dom.apiBootTrack.setAttribute("aria-valuenow", String(nextPercent));
  }
  if (dom.apiBootBar) {
    dom.apiBootBar.style.width = nextPercent + "%";
  }
  if (dom.apiBootPercent) {
    dom.apiBootPercent.textContent = nextPercent + "%";
  }
  if (dom.apiBootPhase) {
    dom.apiBootPhase.textContent = nextPhase;
  }
  if (dom.bootSplashTrack) {
    dom.bootSplashTrack.setAttribute("aria-valuenow", String(nextPercent));
  }
  if (dom.bootSplashBar) {
    dom.bootSplashBar.style.width = nextPercent + "%";
  }
  if (dom.bootSplashPercent) {
    dom.bootSplashPercent.textContent = nextPercent + "%";
  }
  if (dom.bootSplashPhase) {
    dom.bootSplashPhase.textContent = nextPhase;
  }
  updateBootSplashSteps(nextPercent);
}

function setApiBootWaiting(phase) {
  const nextPhase = phase || "Waiting for API response";
  state.apiBootProgress = 0;
  setBootSplashVisible(true);
  if (dom.apiBootProgress) {
    dom.apiBootProgress.classList.remove("is-hidden");
    dom.apiBootProgress.setAttribute("aria-hidden", "false");
  }
  if (dom.apiBootTrack) {
    dom.apiBootTrack.setAttribute("aria-valuenow", "0");
  }
  if (dom.apiBootBar) {
    dom.apiBootBar.style.width = "0%";
  }
  if (dom.apiBootPercent) {
    dom.apiBootPercent.textContent = "--";
  }
  if (dom.apiBootPhase) {
    dom.apiBootPhase.textContent = nextPhase;
  }
  if (dom.bootSplashTrack) {
    dom.bootSplashTrack.setAttribute("aria-valuenow", "0");
  }
  if (dom.bootSplashBar) {
    dom.bootSplashBar.style.width = "0%";
  }
  if (dom.bootSplashPercent) {
    dom.bootSplashPercent.textContent = "--";
  }
  if (dom.bootSplashPhase) {
    dom.bootSplashPhase.textContent = nextPhase;
  }
  updateBootSplashSteps(0);
}

function hideApiBootProgressSoon() {
  window.setTimeout(() => {
    if (state.apiBase) {
      setApiBootProgress(100, "API connection complete", { hide: true });
    }
  }, 650);
}

function hasReadyRuntimeServices(payload) {
  return window.EvelynBootProgress.hasReadyRuntimeServices(payload);
}

function bootProgressFromRuntimeServices(payload) {
  return window.EvelynBootProgress.fromRuntimeServices(payload);
}

function shouldRevealControlSurfaceDuringBoot(payload) {
  return window.EvelynBootProgress.isReady(payload);
}

function applyBootProgressPayload(payload) {
  const progress = window.EvelynBootProgress.progressFromPayload(payload);
  if (!progress || typeof progress !== "object") {
    return false;
  }
  const percent = clampPercent(progress.percent);
  const phase = progress.phase || "Checking boot progress";
  const componentsReady = shouldRevealControlSurfaceDuringBoot(payload);
  setApiBootProgress(componentsReady ? 100 : percent, componentsReady ? "Control Ready" : phase, { hide: componentsReady });
  if (componentsReady) {
    hideApiBootProgressSoon();
  }
  return true;
}

function autosizeTextarea() {
  if (!dom.commandInput) {
    return;
  }
  dom.commandInput.style.height = "0px";
  dom.commandInput.style.height = Math.min(dom.commandInput.scrollHeight, 160) + "px";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatTimestamp(value) {
  if (!value) {
    return "--:--";
  }
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) {
    return "--:--";
  }
  return date.toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatWaitDuration(ms) {
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min <= 0) {
    return sec + "s";
  }
  return min + "m " + String(sec).padStart(2, "0") + "s";
}

function stopApiWaitingTicker() {
  if (apiWaitingTicker !== null) {
    clearInterval(apiWaitingTicker);
    apiWaitingTicker = null;
  }
}

function buildApiWaitingMessages() {
  const startedAt = state.apiWaitStartedAt || Date.now();
  const elapsedMs = Date.now() - startedAt;
  const phaseIndex = Math.floor(elapsedMs / 1600) % apiWaitingSequence.length;
  const hintIndex = Math.floor(elapsedMs / 2600) % apiWaitingHints.length;
  const elapsedLabel = formatWaitDuration(elapsedMs);
  return [
    {
      role: "assistant",
      author: "Control",
      text: apiWaitingSequence[phaseIndex],
      at: Date.now() / 1000,
    },
    {
      role: "assistant",
      author: "Status",
      text: "127.0.0.1:8799 API waiting - elapsed " + elapsedLabel,
      at: Date.now() / 1000,
    },
    {
      role: "assistant",
      author: "Hint",
      text: apiWaitingHints[hintIndex],
      at: Date.now() / 1000,
    },
  ];
}

function renderApiWaitingState({ preserveScroll = false } = {}) {
  if (!state.apiWaitStartedAt) {
    state.apiWaitStartedAt = Date.now();
  }
  const elapsedMs = Date.now() - state.apiWaitStartedAt;
  const phaseIndex = Math.floor(elapsedMs / 1600) % apiWaitingSequence.length;
  const elapsedLabel = formatWaitDuration(elapsedMs);
  setApiBootWaiting(apiWaitingSequence[phaseIndex]);
  renderChat(
    buildApiWaitingMessages(),
    "Waiting for the local Control-Page API response.",
    { preserveScroll }
  );
  const ui = { mode: "default", submode: "booting", reason: "api_waiting" };
  applyUiMode(ui);
  setStateClasses(dom.modePill, ["is-default"], ["is-default", "is-minecraft", "is-warmup", "is-issue", "is-offline"]);
  setStateClasses(dom.submodePill, ["is-warmup"], ["is-default", "is-minecraft", "is-warmup", "is-issue", "is-offline"]);
  setStateClasses(dom.systemSummaryPill, ["is-warmup"], ["is-idle", "is-active", "is-warmup", "is-issue", "is-offline"]);
  setStateClasses(dom.voicePresencePill, ["is-warmup"], ["is-idle", "is-active", "is-warmup", "is-issue", "is-offline"]);
  if (dom.modePill) dom.modePill.textContent = uiModeLabel(ui.mode);
  if (dom.submodePill) dom.submodePill.textContent = uiSubmodeLabel(ui.submode);
  if (dom.topbarStatusLine) dom.topbarStatusLine.textContent = "Waiting for the local Control-Page API response.";
  if (dom.systemSummaryPill) dom.systemSummaryPill.textContent = "booting";
  if (dom.voicePresencePill) dom.voicePresencePill.textContent = "preparing";
  if (dom.operationsEyebrow) dom.operationsEyebrow.textContent = "OPERATIONS FEED";
  if (dom.operationsTitle) dom.operationsTitle.textContent = "Boot flow";
  if (dom.operationsSubcopy) dom.operationsSubcopy.textContent = "Evelyn is bringing local runtime services online.";
  if (dom.actionsEyebrow) dom.actionsEyebrow.textContent = "CONTROL ACTIONS";
  if (dom.actionsSubcopy) dom.actionsSubcopy.textContent = "Controls and chat will update when the API responds.";
  if (dom.primaryActionTitle) dom.primaryActionTitle.textContent = "Booting";
  if (dom.supportActionTitle) dom.supportActionTitle.textContent = "Waiting";
  if (dom.supportActionCaption) dom.supportActionCaption.textContent = "Live controls are disabled until the API is ready.";
  if (dom.avatarStatusCopy) dom.avatarStatusCopy.textContent = "Control-Page is checking local API readiness.";
  if (dom.composerHintLeft) dom.composerHintLeft.textContent = "Waiting for API";
  if (dom.controlBriefTitle) dom.controlBriefTitle.textContent = "Checking connection";
  if (dom.controlBriefBody) dom.controlBriefBody.textContent = "Evelyn and the Control-Page API have not responded yet.";
  if (dom.controlNextTitle) dom.controlNextTitle.textContent = "Current check";
  if (dom.controlNextBody) dom.controlNextBody.textContent = "When start.bat and the API are ready, recommended actions will appear here.";
  if (dom.controlIssueCard) dom.controlIssueCard.classList.remove("control-hidden");
  if (dom.controlIssueTitle) dom.controlIssueTitle.textContent = "Waiting state";
  if (dom.controlIssueBody) dom.controlIssueBody.textContent = "Waiting for 127.0.0.1:8799 to respond.";
  if (dom.quickCommandCaption) dom.quickCommandCaption.textContent = "Command buttons will be available after the API is ready.";
  if (dom.operatorRuntimeTitle) dom.operatorRuntimeTitle.textContent = "Checking connection";
  if (dom.operatorRuntimeSubcopy) dom.operatorRuntimeSubcopy.textContent = "Waiting for the Control-Page API response.";
  if (dom.operatorRuntimeNote) dom.operatorRuntimeNote.textContent = "Evelyn, voice, Voyager, and runtime status are being checked.";
  if (dom.operatorStatChannel) dom.operatorStatChannel.textContent = "standby";
  if (dom.operatorStatMode) dom.operatorStatMode.textContent = "booting";
  if (dom.operatorStatTts) dom.operatorStatTts.textContent = "0";
  if (dom.operatorStatLlm) dom.operatorStatLlm.textContent = "0";
  if (dom.defaultFocusTitle) dom.defaultFocusTitle.textContent = "Checking connection";
  if (dom.defaultFocusBody) dom.defaultFocusBody.textContent = "Evelyn is waiting for the local API before loading controls.";
  if (dom.defaultFocusRecentTitle) dom.defaultFocusRecentTitle.textContent = "Recent activity";
  if (dom.defaultFocusRecentBody) dom.defaultFocusRecentBody.textContent = "Recent assistant replies will appear after connection.";
  if (dom.defaultFocusContextTitle) dom.defaultFocusContextTitle.textContent = "Booting";
  if (dom.defaultFocusContextBody) dom.defaultFocusContextBody.textContent = "Mode booting - waiting for API and runtime context.";
  setMeter(dom.meterVoyager, dom.meterVoyagerLabel, 26 + (phaseIndex * 12), "waiting");
  setMeter(dom.meterVoice, dom.meterVoiceLabel, 18, "standby");
  setMeter(dom.meterTts, dom.meterTtsLabel, 8, "idle");
  setMeter(dom.meterLlm, dom.meterLlmLabel, 12, "warming");
  renderActivityRows([
    { kind: "done", label: "boot sequence", detail: "start.bat launched" },
    { kind: "done", label: "localhost probe", detail: "127.0.0.1:8799 retrying" },
    { kind: "failed", label: "elapsed", detail: elapsedLabel + " waiting" },
  ]);
  const waitingButtons = [
    '<button type="button" class="quick-command" disabled>booting</button>',
    '<button type="button" class="quick-command" disabled>voice</button>',
    '<button type="button" class="quick-command" disabled>voyager</button>',
    '<button type="button" class="quick-command" disabled>control api</button>',
  ].join("");
  if (dom.primaryActionRow) dom.primaryActionRow.innerHTML = waitingButtons;
  if (dom.quickCommandRow) dom.quickCommandRow.innerHTML = waitingButtons;
}
function ensureApiWaitingTicker() {
  if (!state.apiWaitStartedAt) {
    state.apiWaitStartedAt = Date.now();
  }
  renderApiWaitingState();
  if (apiWaitingTicker !== null) {
    return;
  }
  apiWaitingTicker = window.setInterval(() => {
    if (state.apiBase) {
      stopApiWaitingTicker();
      return;
    }
    renderApiWaitingState({ preserveScroll: true });
  }, 1400);
}

function setMeter(node, labelNode, percent, label) {
  const rawPercent = Math.max(0, Math.min(100, Number(percent) || 0));
  const normalizedPercent = rawPercent >= 90
    ? 100
    : (rawPercent >= 55 ? 68 : (rawPercent >= 20 ? 42 : 16));
  if (node) {
    node.style.width = normalizedPercent + "%";
    node.dataset.rawPercent = String(Math.round(rawPercent));
  }
  if (labelNode) {
    labelNode.textContent = label;
  }
}

function initializeInventoryWidget() {
  if (!dom.inventoryCard || !dom.inventoryWidget || !dom.inventorySummary || !dom.inventoryWidgetList || !dom.inventoryToggleButton) {
    return;
  }
  dom.inventoryCard.classList.add("has-inventory-widget");
  if (!dom.inventoryWidgetList.children.length) {
    dom.inventoryWidgetList.innerHTML = '<p class="inventory-widget-empty">No inventory snapshot yet.</p>';
  }
}

function setInventoryWidgetOpen(open) {
  const nextOpen = Boolean(open);
  state.inventoryWidgetOpen = nextOpen;
  if (dom.inventoryWidget) {
    dom.inventoryWidget.classList.toggle("is-hidden", !nextOpen);
    dom.inventoryWidget.setAttribute("aria-hidden", String(!nextOpen));
  }
  if (dom.inventoryToggleButton) {
    dom.inventoryToggleButton.setAttribute("aria-expanded", String(nextOpen));
  }
  if (dom.inventoryCard) {
    dom.inventoryCard.classList.toggle("is-widget-open", nextOpen);
  }
}

function inventoryApiUrl(path) {
  if (state.apiBase) {
    return state.apiBase + path;
  }
  if (location.protocol.startsWith("http")) {
    return location.origin + path;
  }
  return path;
}

function humanizeMinecraftItemName(name) {
  const normalized = String(name || "").trim().replace(/^minecraft:/, "");
  if (!normalized) {
    return "Unknown";
  }
  return normalized.replaceAll("_", " ");
}

function inventorySlotLabel(name) {
  const label = humanizeMinecraftItemName(name)
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0] || "")
    .join("")
    .toUpperCase();
  return label || "?";
}

function inventoryIconUrl(name) {
  if (!name) {
    return "";
  }
  return inventoryApiUrl("/api/control-page/minecraft-item-icon/" + encodeURIComponent(String(name).replace(/^minecraft:/, "")));
}

function buildInventorySectionSlots(rawSlots, entries) {
  const sectionSpecs = {
    armor: { count: 4, labels: ["helmet", "chestplate", "leggings", "boots"] },
    main: { count: 27, labels: Array.from({ length: 27 }, (_value, index) => String(index + 1)) },
    hotbar: { count: 9, labels: Array.from({ length: 9 }, (_value, index) => String(index + 1)) },
    offhand: { count: 1, labels: ["offhand"] },
  };
  const sections = {};
  for (const [section, spec] of Object.entries(sectionSpecs)) {
    sections[section] = Array.from({ length: spec.count }, (_value, index) => ({
      section,
      sectionIndex: index,
      label: spec.labels[index] || "",
      item: null,
      count: 0,
      displayName: "",
      selected: false,
    }));
  }
  if (Array.isArray(rawSlots) && rawSlots.length) {
    for (const slot of rawSlots) {
      if (!slot || typeof slot !== "object") {
        continue;
      }
      const section = slot.section;
      const sectionIndex = Number(slot.sectionIndex);
      if (!sections[section] || !Number.isFinite(sectionIndex) || !sections[section][sectionIndex]) {
        continue;
      }
      sections[section][sectionIndex] = {
        ...sections[section][sectionIndex],
        item: slot.item || null,
        count: Number(slot.count) > 0 ? Number(slot.count) : 0,
        displayName: slot.displayName || humanizeMinecraftItemName(slot.item),
        selected: Boolean(slot.selected),
      };
    }
    return sections;
  }
  const fallbackTargets = sections.main.concat(sections.hotbar);
  (Array.isArray(entries) ? entries : []).slice(0, fallbackTargets.length).forEach((entry, index) => {
    const target = fallbackTargets[index];
    target.item = entry && entry.name ? entry.name : null;
    target.count = entry && Number(entry.count) > 0 ? Number(entry.count) : 0;
    target.displayName = humanizeMinecraftItemName(target.item);
  });
  return sections;
}

function renderInventorySlot(slot) {
  const itemName = slot && slot.item ? String(slot.item) : "";
  const displayName = slot && slot.displayName ? slot.displayName : (itemName ? humanizeMinecraftItemName(itemName) : "Empty slot");
  const classes = ["inventory-slot"];
  if (!itemName) {
    classes.push("is-empty");
  }
  if (slot && slot.selected) {
    classes.push("is-selected");
  }
  const countMarkup = slot && slot.count > 1
    ? '<span class="inventory-slot-count">' + escapeHtml(String(slot.count)) + "</span>"
    : "";
  const iconMarkup = itemName
    ? '<img class="inventory-slot-icon" src="' + escapeHtml(inventoryIconUrl(itemName)) + '" alt="' + escapeHtml(displayName) + '" loading="lazy" onerror="this.remove()">'
    : "";
  return [
    '<div class="' + classes.join(" ") + '" title="' + escapeHtml(displayName) + '">',
    '<span class="inventory-slot-fallback">' + escapeHtml(itemName ? inventorySlotLabel(itemName) : "") + "</span>",
    iconMarkup,
    countMarkup,
    "</div>",
  ].join("");
}

function renderInventoryLedger(entries) {
  if (!Array.isArray(entries) || !entries.length) {
    return "";
  }
  return [
    '<div class="inventory-ledger">',
    entries.slice(0, 8).map((row) => {
      const itemName = row && row.name ? String(row.name) : "";
      const count = row && row.count != null ? String(row.count) : "-";
      return [
        '<div class="inventory-ledger-item">',
        '<span class="inventory-ledger-icon"><img src="' + escapeHtml(inventoryIconUrl(itemName)) + '" alt="' + escapeHtml(humanizeMinecraftItemName(itemName)) + '" loading="lazy" onerror="this.remove()"><span>' + escapeHtml(inventorySlotLabel(itemName)) + "</span></span>",
        '<span class="inventory-ledger-name">' + escapeHtml(humanizeMinecraftItemName(itemName)) + "</span>",
        "<strong>x" + escapeHtml(count) + "</strong>",
        "</div>",
      ].join("");
    }).join(""),
    "</div>",
  ].join("");
}

function renderInventoryWidget(summary, entries, rawSlots, usedSlots, uniqueItemCount) {
  if (dom.inventorySummary) {
    dom.inventorySummary.textContent = summary || "No inventory info";
  }
  if (!dom.inventoryWidgetList) {
    return;
  }
  const sections = buildInventorySectionSlots(rawSlots, entries);
  const hasAnyItem = Object.values(sections).some((rows) => rows.some((row) => row.item));
  const hasSlotLayout = Object.values(sections).some((rows) => Array.isArray(rows) && rows.length);
  if (!Array.isArray(entries)) {
    entries = [];
  }
  if (!entries.length && hasAnyItem) {
    entries = sections.main
      .concat(sections.hotbar, sections.armor, sections.offhand)
      .filter((row) => row.item)
      .slice(0, 8)
      .map((row) => ({ name: row.item, count: row.count }));
  }
  const normalizedUsed = Number.isFinite(Number(usedSlots))
    ? Number(usedSlots)
    : sections.main.concat(sections.hotbar).filter((row) => row.item).length;
  const normalizedUnique = Number.isFinite(Number(uniqueItemCount))
    ? Number(uniqueItemCount)
    : entries.length;
  if (dom.inventorySummary) {
    dom.inventorySummary.textContent = hasSlotLayout
      ? (hasAnyItem ? (String(normalizedUsed) + "/36 used") : "0/36 used - empty")
      : "No inventory info";
  }
  if ((!Array.isArray(entries) || !entries.length) && !hasSlotLayout) {
    dom.inventoryWidgetList.innerHTML = '<p class="inventory-widget-empty">No inventory items to show.</p>';
    return;
  }
  dom.inventoryWidgetList.innerHTML = [
    '<div class="inventory-widget-meta">',
    '<span class="inventory-widget-stat">' + escapeHtml(String(normalizedUsed)) + '/36 used</span>',
    '<span class="inventory-widget-stat">' + escapeHtml(String(normalizedUnique)) + ' types</span>',
    '</div>',
    '<div class="inventory-board">',
    '<div class="inventory-side-column">',
    '<p class="inventory-section-title">Armor</p>',
    '<div class="inventory-grid inventory-grid-armor">' + sections.armor.map(renderInventorySlot).join("") + '</div>',
    '</div>',
    '<div class="inventory-main-column">',
    '<p class="inventory-section-title">Inventory</p>',
    '<div class="inventory-grid inventory-grid-main">' + sections.main.map(renderInventorySlot).join("") + '</div>',
    '<p class="inventory-section-title inventory-section-title-hotbar">Hotbar</p>',
    '<div class="inventory-grid inventory-grid-hotbar">' + sections.hotbar.map(renderInventorySlot).join("") + '</div>',
    '</div>',
    '<div class="inventory-side-column inventory-side-column-offhand">',
    '<p class="inventory-section-title">Offhand</p>',
    '<div class="inventory-grid inventory-grid-offhand">' + sections.offhand.map(renderInventorySlot).join("") + '</div>',
    '</div>',
    '</div>',
    renderInventoryLedger(entries),
  ].join("");
}

function cleanDisplayText(value, fallback = "None") {
  const text = value == null ? "" : String(value).trim();
  return text || fallback;
}

const RUNTIME_HEALTH_DIAGNOSIS_TEXT = {
  CP_UP_BOT_DOWN: {
    issue: "Control-Page is up, but Bot API is down.",
    detail: "The page can load, but chat, memory commands, and runtime commands can fail.",
    repairActionLabel: "Preview Bot API restart",
    repairActionSummary: "Preview the Bot API repair plan before starting anything.",
  },
  BOT_API_DOWN_WITH_CONTROL_PAGE_UP: {
    issue: "Control-Page is open, but Bot API is not responding.",
    detail: "Chat, memory, and runtime commands are limited until Bot API responds.",
    repairActionLabel: "Preview Bot API restart",
    repairActionSummary: "Preview the Bot API repair plan before starting anything.",
  },
  BOT_API_PARTIAL: {
    issue: "Bot API is partially responding or failing some requests.",
    detail: "Some features may work intermittently until Bot API stabilizes.",
    repairActionLabel: "Preview Bot API health repair",
    repairActionSummary: "Check Bot API health and preview repair if needed.",
  },
  CONTROL_PAGE_DOWN: {
    issue: "Control-Page server is not responding.",
    detail: "The UI and command input can be unavailable until Control-Page recovers.",
    repairActionLabel: "Preview Control-Page restart",
    repairActionSummary: "Preview Control-Page repair before starting anything.",
  },
  MAIN_LLM_DOWN: {
    issue: "Main LLM is not responding.",
    detail: "Main conversation generation is limited until Main LLM recovers.",
    repairActionLabel: "Preview Main LLM repair",
    repairActionSummary: "Preview Main LLM repair before starting anything.",
  },
  ROUTER_LLM_DOWN: {
    issue: "Router LLM is not responding.",
    detail: "Routing decisions may be limited until Router LLM recovers.",
    repairActionLabel: "Preview Router LLM repair",
    repairActionSummary: "Preview Router LLM repair before starting anything.",
  },
  SUB_LLM_DOWN: {
    issue: "Sub LLM is not responding.",
    detail: "Support judgment and summarization may be limited until Sub LLM recovers.",
    repairActionLabel: "Preview Sub LLM repair",
    repairActionSummary: "Preview Sub LLM repair before starting anything.",
  },
  TTS_DOWN: {
    issue: "TTS is not responding.",
    detail: "Voice output is unavailable until TTS recovers.",
    repairActionLabel: "Preview TTS repair",
    repairActionSummary: "Preview TTS repair before starting anything.",
  },
};

const RUNTIME_STATE_TEXT_BY_NAME = {
  up: "up",
  degraded: "degraded",
  down: "down",
  recovering: "recovering",
  startup: "starting",
};

function normalizedRuntimeCode(code) {
  return String(code || "").toUpperCase();
}

function runtimeHealthCodeText(health) {
  const diagnostic = primaryRuntimeDiagnostic(health);
  const issueCode = normalizedRuntimeCode(diagnostic && diagnostic.code);
  return RUNTIME_HEALTH_DIAGNOSIS_TEXT[issueCode] || null;
}

function runtimeHealthFromPayload(payload) {
  const runtime = (payload && payload.runtime) || {};
  const health = runtime.serviceHealth || payload.serviceHealth || null;
  return health && typeof health === "object" ? health : null;
}

function runtimeHealthDiagnostics(health) {
  return Array.isArray(health && health.diagnostics) ? health.diagnostics : [];
}

function primaryRuntimeDiagnostic(health) {
  const diagnostics = runtimeHealthDiagnostics(health).filter((item) => item && typeof item === "object");
  const severityRank = { error: 0, warning: 1, info: 2 };
  diagnostics.sort((a, b) => {
    const left = severityRank[String(a.severity || "info").toLowerCase()] ?? 3;
    const right = severityRank[String(b.severity || "info").toLowerCase()] ?? 3;
    return left - right;
  });
  return diagnostics[0] || null;
}

function runtimeHealthHasIssue(health) {
  if (!health || typeof health !== "object") {
    return false;
  }
  const stateName = String(health.overallState || "").toLowerCase();
  if (stateName === "down" || stateName === "degraded") {
    return true;
  }
  return runtimeHealthDiagnostics(health).some((item) => {
    const severity = String((item && item.severity) || "").toLowerCase();
    return severity === "error" || severity === "warning";
  });
}

function runtimeHealthIssueText(health) {
  const diagnostic = primaryRuntimeDiagnostic(health);
  const codeText = runtimeHealthCodeText(health);
  if (codeText && codeText.issue) {
    return cleanDisplayText(codeText.issue, "Runtime status needs attention.");
  }
  if (diagnostic && (diagnostic.message || diagnostic.code)) {
    return "Runtime diagnosis: " + cleanDisplayText(diagnostic.message || diagnostic.code, "No diagnosis message.");
  }
  if (health && health.overallState) {
    const stateName = String(health.overallState || "").toLowerCase();
    return RUNTIME_STATE_TEXT_BY_NAME[stateName] || ("Runtime state: " + cleanDisplayText(health.overallState, "unknown"));
  }
  if (health && health.summary) {
    return "Runtime state: " + cleanDisplayText(health.summary, "unknown");
  }
  return "";
}

function runtimeHealthDetailText(health) {
  const diagnostic = primaryRuntimeDiagnostic(health);
  const codeText = runtimeHealthCodeText(health);
  if (codeText && codeText.detail) {
    return cleanDisplayText(codeText.detail, "No runtime detail is available.");
  }
  if (diagnostic && (diagnostic.details || diagnostic.message || diagnostic.code)) {
    return "Detail: " + cleanDisplayText(diagnostic.details || diagnostic.message || diagnostic.code, "No diagnosis detail.");
  }
  if (health && health.summary) {
    return "Summary: " + cleanDisplayText(health.summary, "No summary.");
  }
  return "";
}

function formatMetricMs(value) {
  if (value == null) {
    return "-";
  }
  const numberValue = Number(value || 0);
  if (!Number.isFinite(numberValue) || numberValue <= 0) {
    return "0ms";
  }
  return Math.round(numberValue) + "ms";
}

function formatMetricPercent(value) {
  if (value == null) {
    return "-";
  }
  const numberValue = Number(value || 0);
  if (!Number.isFinite(numberValue) || numberValue <= 0) {
    return "0%";
  }
  return Math.round(numberValue * 100) + "%";
}

function topAskMode(distribution) {
  const rows = Object.entries(distribution || {}).filter(([, count]) => Number(count || 0) > 0);
  if (!rows.length) {
    return "none";
  }
  rows.sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0) || String(a[0]).localeCompare(String(b[0])));
  return String(rows[0][0] || "none");
}

function setStateClasses(node, activeClasses, managedClasses) {
  if (!node) {
    return;
  }
  for (const className of managedClasses) {
    node.classList.toggle(className, activeClasses.includes(className));
  }
}

function uiModeLabel(mode) {
  return mode === "minecraft" ? "minecraft mode" : "default mode";
}

function uiSubmodeLabel(submode) {
  switch (submode) {
    case "minecraft-live":
      return "live session";
    case "voyager-warmup":
      return "warmup";
    case "voice-speaking":
      return "speaking";
    case "voice-listening":
      return "listening";
    case "offline":
      return "offline";
    case "issue":
      return "issue";
    case "stale":
      return "stale";
    case "booting":
      return "booting";
    default:
      return cleanDisplayText(submode, "idle");
  }
}

function summaryPillState(ui, hasIssue) {
  if (hasIssue || ui.submode === "issue" || ui.submode === "stale") {
    return "is-issue";
  }
  if (ui.submode === "offline") {
    return "is-offline";
  }
  if (ui.mode === "minecraft") {
    return "is-active";
  }
  if (ui.submode === "voyager-warmup" || ui.submode === "booting") {
    return "is-warmup";
  }
  return "is-idle";
}

function presencePillState(ui, voice) {
  if (ui.submode === "offline") {
    return "is-offline";
  }
  if (voice && voice.speaking) {
    return "is-active";
  }
  if (voice && voice.listening) {
    return "is-active";
  }
  if (ui.submode === "voyager-warmup" || ui.submode === "booting") {
    return "is-warmup";
  }
  if (ui.submode === "issue" || ui.submode === "stale") {
    return "is-issue";
  }
  return "is-idle";
}

function commandDisplayName(item) {
  const command = String((item && item.command) || "").toLowerCase();
  switch (command) {
    case "/status":
      return "Evelyn status";
    case "/voice input auto":
      return "Voice auto";
    case "/voice input local":
      return "Local mic";
    case "/voice input discord":
      return "Discord voice";
    case "/inventory":
      return "Inventory";
    case "/voyager stats":
      return "Voyager stats";
    case "/minecraft status":
      return "Minecraft status";
    case "/minecraft connect":
      return "Minecraft connect";
    case "/minecraft disconnect":
      return "Minecraft disconnect";
    case "/autonomy status":
      return "Autonomy status";
    case "/help":
      return "Help";
    case "/memory":
    case "/obsidian":
      return "Memory";
    case "/shutdown":
      return "Shutdown";
    default:
      return item && (item.command || item.template) ? String(item.command || item.template) : "Command";
  }
}

function pickCommandsByExact(commands, ordered) {
  const picked = [];
  const seen = new Set();
  for (const rawCommand of ordered) {
    const target = String(rawCommand || "").toLowerCase();
    const item = commands.find((entry) => String((entry && entry.command) || "").toLowerCase() === target);
    if (!item) {
      continue;
    }
    const key = item.template || item.command;
    if (!key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    picked.push(item);
  }
  return picked;
}

function buildPrimaryActions(payload, commands) {
  const minecraft = (payload && payload.minecraft) || {};
  if (minecraft.sessionActive) {
    return pickCommandsByExact(commands, [
      "/minecraft status",
      "/inventory",
      "/voyager stats",
      "/minecraft disconnect",
    ]);
  }
  if (minecraft.running) {
    return pickCommandsByExact(commands, [
      "/minecraft status",
      "/status",
      "/voyager stats",
      "/minecraft disconnect",
    ]);
  }
  return pickCommandsByExact(commands, [
    "/minecraft connect",
    "/status",
    "/autonomy status",
    "/help",
  ]);
}

function buildSupportActions(payload, commands, primaryActions) {
  const minecraft = (payload && payload.minecraft) || {};
  const primaryKeys = new Set(primaryActions.map((item) => item.template || item.command));
  const preferred = minecraft.sessionActive
    ? ["/status", "/help", "/autonomy status"]
    : ["/status", "/help", "/autonomy status"];
  const supplemental = pickCommandsByExact(commands, preferred).filter((item) => !primaryKeys.has(item.template || item.command));
  const fallback = commands.filter((item) => {
    const key = item.template || item.command;
    if (!key || primaryKeys.has(key)) {
      return false;
    }
    const command = String(item.command || "").toLowerCase();
    return command !== "/shutdown" && command !== "/minecraft goal <goal>";
  });
  const merged = [];
  const seen = new Set();
  for (const item of supplemental.concat(fallback)) {
    const key = item.template || item.command;
    if (!key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    merged.push(item);
  }
  return merged.slice(0, 4);
}

function commandButtonMarkup(items) {
  return items.map((item) => (
    item.repairPreview
      ? (
        '<button type="button" class="quick-command is-repair-preview" data-runtime-repair-preview="1" data-repair-action-id="' + escapeHtml(item.actionId || "") + '" data-repair-service-id="' + escapeHtml(item.serviceId || "") + '" title="' + escapeHtml(item.summary || "") + '">' +
          escapeHtml(item.label || "Preview repair") +
        "</button>"
      )
      : (
        '<button type="button" class="quick-command' + (((item.command || "") === "/shutdown") ? ' is-danger' : '') + '" data-chat-command="' + escapeHtml(item.template || item.command) + '" data-chat-send="0" title="' + escapeHtml(item.summary || "") + '">' +
          escapeHtml(commandDisplayName(item)) +
        "</button>"
      )
  )).join("");
}

function describeControlState(payload) {
  const guild = (payload && payload.guild) || {};
  const voice = (payload && payload.voice) || {};
  const runtime = (payload && payload.runtime) || {};
  const services = runtime.services || {};
  const controlPlane = runtime.controlPlane || {};
  const serviceHealth = runtimeHealthFromPayload(payload);
  const minecraft = (payload && payload.minecraft) || {};
  const issues = [];
  const runtimeIssue = runtimeHealthIssueText(serviceHealth);
  if (runtimeIssue) {
    issues.push("Runtime: " + runtimeIssue);
  }
  if (minecraft.lastError) {
    issues.push("Minecraft error: " + cleanDisplayText(minecraft.lastError, "-"));
  }
  if (minecraft.snapshotExpired) {
    issues.push("Minecraft snapshot expired. Refresh the session state.");
  } else if (minecraft.snapshotStale) {
    issues.push("Minecraft snapshot is stale. Waiting for a fresh state update.");
  }
  if (services.voyagerError) {
    issues.push("Voyager: " + cleanDisplayText(services.voyagerError, "-"));
  }
  if (services.codexError) {
    issues.push("Codex: " + cleanDisplayText(services.codexError, "-"));
  }

  const base = {
    issueTitle: issues.length ? "Issue" : "",
    issueBody: issues[0] || "",
    showIssue: Boolean(issues.length),
  };

  if (!guild.name) {
    return {
      ...base,
      title: "Waiting for Discord connection",
      body: "Evelyn is waiting for guild and runtime state.",
      nextTitle: "Next",
      nextBody: "Check status or send a command when the connection is ready.",
      quickCaption: "Use a quick command to inspect the runtime.",
    };
  }

  if (minecraft.sessionActive) {
    const focus = cleanDisplayText(minecraft.task, "") || cleanDisplayText(minecraft.goal, "idle");
    return {
      ...base,
      title: "Minecraft session active",
      body: focus === "idle" ? "Minecraft is connected and waiting for the next task." : "Current task: " + focus,
      nextTitle: "Next",
      nextBody: "Use inventory, status, or Voyager stats to inspect the session.",
      quickCaption: "Minecraft controls are ready.",
    };
  }

  if (minecraft.running) {
    return {
      ...base,
      title: "Voyager preparing",
      body: "Voyager is running while Minecraft session state is still warming up.",
      nextTitle: "Next",
      nextBody: "Check Minecraft status before issuing live session commands.",
      quickCaption: "Voyager and Minecraft checks are available.",
    };
  }

  if (voice.speaking) {
    return {
      ...base,
      title: "Evelyn speaking",
      body: cleanDisplayText(voice.ttsTargetName, "voice output") + " is receiving TTS output.",
      nextTitle: "Next",
      nextBody: "Wait for speech to finish or check voice status.",
      quickCaption: "Voice controls are available.",
    };
  }

  return {
    ...base,
    title: "Evelyn ready",
    body: voice.listening ? "Voice input is listening." : "Runtime is ready for commands.",
    nextTitle: "Next",
    nextBody: "Use status, memory, or Minecraft commands from the command bar.",
    quickCaption: "Quick commands are ready.",
  };
}

function renderControlBrief(payload) {
  const brief = describeControlState(payload);
  if (dom.controlBriefTitle) {
    dom.controlBriefTitle.textContent = brief.title;
  }
  if (dom.controlBriefBody) {
    dom.controlBriefBody.textContent = brief.body;
  }
  if (dom.controlNextTitle) {
    dom.controlNextTitle.textContent = brief.nextTitle;
  }
  if (dom.controlNextBody) {
    dom.controlNextBody.textContent = brief.nextBody;
  }
  if (dom.quickCommandCaption) {
    dom.quickCommandCaption.textContent = brief.quickCaption;
  }
  if (dom.controlIssueCard) {
    dom.controlIssueCard.classList.toggle("control-hidden", !brief.showIssue);
  }
  if (dom.controlIssueTitle) {
    dom.controlIssueTitle.textContent = brief.issueTitle || "Attention";
  }
  if (dom.controlIssueBody) {
    dom.controlIssueBody.textContent = brief.issueBody || "No issue details available.";
  }
}

function latestChatRow(messages, role) {
  if (!Array.isArray(messages)) {
    return null;
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const row = messages[index];
    if (!row || row.role !== role) {
      continue;
    }
    return row;
  }
  return null;
}

function resolveUiMode(payload) {
  const declared = payload && payload.ui && typeof payload.ui === "object" ? payload.ui : null;
  if (declared && (declared.mode === "default" || declared.mode === "minecraft")) {
    return {
      mode: declared.mode,
      submode: declared.submode || (declared.mode === "minecraft" ? "minecraft-live" : "idle"),
      reason: declared.reason || "",
    };
  }
  const minecraft = (payload && payload.minecraft) || {};
  if (minecraft.sessionActive) {
    return { mode: "minecraft", submode: "minecraft-live", reason: "minecraft_session_active" };
  }
  if (minecraft.running) {
    return { mode: "default", submode: "voyager-warmup", reason: "voyager_running_without_live_session" };
  }
  return { mode: "default", submode: "idle", reason: "fallback_default" };
}

function applyUiMode(ui) {
  const mode = ui && ui.mode === "minecraft" ? "minecraft" : "default";
  document.body.classList.toggle("is-default-mode", mode === "default");
  document.body.classList.toggle("is-minecraft-mode", mode === "minecraft");
  document.body.dataset.mode = mode;
  document.body.dataset.submode = ui && ui.submode ? String(ui.submode) : "";
  if (dom.controlPageRoot) {
    dom.controlPageRoot.classList.toggle("is-default-mode", mode === "default");
    dom.controlPageRoot.classList.toggle("is-minecraft-mode", mode === "minecraft");
    dom.controlPageRoot.dataset.mode = mode;
    dom.controlPageRoot.dataset.submode = ui && ui.submode ? String(ui.submode) : "";
  }
}

function renderDefaultViewport(payload, ui) {
  const voice = (payload && payload.voice) || {};
  const guild = (payload && payload.guild) || {};
  const runtime = (payload && payload.runtime) || {};
  const services = runtime.services || {};
  const messages = ((payload && payload.chat) || {}).messages || [];
  const latestAssistant = latestChatRow(messages, "assistant");
  const latestUser = latestChatRow(messages, "user");
  const brief = describeControlState(payload);
  if (dom.defaultFocusTitle) {
    dom.defaultFocusTitle.textContent = brief.title;
  }
  if (dom.defaultFocusBody) {
    dom.defaultFocusBody.textContent = brief.body;
  }
  if (dom.defaultFocusRecentTitle) {
    dom.defaultFocusRecentTitle.textContent = latestAssistant ? cleanDisplayText(latestAssistant.author, "Evelyn") : "No recent assistant message";
  }
  if (dom.defaultFocusRecentBody) {
    dom.defaultFocusRecentBody.textContent = latestAssistant ? cleanDisplayText(latestAssistant.text, "No assistant reply yet.") : "Recent assistant replies will appear here.";
  }
  if (dom.defaultFocusContextTitle) {
    dom.defaultFocusContextTitle.textContent = guild.name ? cleanDisplayText(guild.name, "Guild") : "Guild not connected";
  }
  if (dom.defaultFocusContextBody) {
    const contextParts = [
      "mode " + cleanDisplayText(ui && ui.submode, "idle"),
      "voice " + (voice.listening ? "ready" : "idle"),
      "tts " + (voice.speaking ? "speaking" : "idle"),
    ];
    if (latestUser && latestUser.text) {
      contextParts.push("last user: " + cleanDisplayText(latestUser.text, ""));
    }
    if (services.voyagerError) {
      contextParts.push("Voyager issue");
    }
    if (services.codexError) {
      contextParts.push("Codex issue");
    }
    dom.defaultFocusContextBody.textContent = contextParts.filter(Boolean).join(" / ");
  }
}

function renderMinecraftOpsPanel(payload) {
  const minecraft = (payload && payload.minecraft) || {};
  if (dom.minecraftOpsTitle) {
    dom.minecraftOpsTitle.textContent = minecraft.task || minecraft.goal || "Minecraft session";
  }
  if (dom.minecraftOpsBody) {
    dom.minecraftOpsBody.textContent = minecraft.progress || minecraft.idleSummary || "Waiting for Minecraft progress.";
  }
  if (dom.minecraftOpsInventoryTitle) {
    dom.minecraftOpsInventoryTitle.textContent = minecraft.inventorySummary || "Inventory summary";
  }
  if (dom.minecraftOpsInventoryBody) {
    const inventoryTop = Array.isArray(minecraft.inventoryTop) ? minecraft.inventoryTop.slice(0, 3) : [];
    dom.minecraftOpsInventoryBody.textContent = inventoryTop.length
      ? inventoryTop.map((item) => cleanDisplayText(item.name, "item") + " x" + String(item.count || 0)).join(", ")
      : "No inventory items are available yet.";
  }
  if (dom.minecraftOpsSurvivalTitle) {
    dom.minecraftOpsSurvivalTitle.textContent = formatHealthHunger(minecraft.health, minecraft.hunger);
  }
  if (dom.minecraftOpsSurvivalBody) {
    const position = cleanDisplayText(minecraft.position, "unknown");
    const hostiles = minecraft.hostiles == null ? "unknown" : String(minecraft.hostiles);
    dom.minecraftOpsSurvivalBody.textContent = "position " + position + " / hostiles " + hostiles;
  }
}

function avatarTalkStart() {
  if (!dom.avatarRoot || !dom.avatarMouth || avatarState.talking) {
    return;
  }
  const frames = [
    avatarFrames.mouth.open,
    avatarFrames.mouth.idle,
    avatarFrames.mouth.o,
    avatarFrames.mouth.open,
    avatarFrames.mouth.closed,
  ];
  let frameIndex = 0;
  avatarState.talking = true;
  dom.avatarRoot.dataset.talking = "true";
  avatarState.talkTimer = window.setInterval(() => {
    dom.avatarMouth.src = frames[frameIndex % frames.length];
    frameIndex += 1;
  }, 115);
}

function avatarTalkStop() {
  if (!dom.avatarRoot || !dom.avatarMouth) {
    return;
  }
  avatarState.talking = false;
  dom.avatarRoot.dataset.talking = "false";
  if (avatarState.talkTimer !== null) {
    window.clearInterval(avatarState.talkTimer);
    avatarState.talkTimer = null;
  }
  dom.avatarMouth.src = avatarFrames.mouth.idle;
}

function avatarBlink(duration = 130) {
  if (!dom.avatarRoot || !dom.avatarEyeLeft || !dom.avatarEyeRight) {
    return;
  }
  dom.avatarRoot.classList.add("is-blinking");
  dom.avatarEyeLeft.src = avatarFrames.eyes.leftBlink;
  dom.avatarEyeRight.src = avatarFrames.eyes.rightBlink;
  window.setTimeout(() => {
    dom.avatarRoot.classList.remove("is-blinking");
    dom.avatarEyeLeft.src = avatarFrames.eyes.leftOpen;
    dom.avatarEyeRight.src = avatarFrames.eyes.rightOpen;
  }, duration);
}

function scheduleAvatarBlink() {
  if (!dom.avatarRoot) {
    return;
  }
  if (avatarState.blinkTimer !== null) {
    window.clearTimeout(avatarState.blinkTimer);
  }
  avatarState.blinkTimer = window.setTimeout(() => {
    avatarBlink();
    scheduleAvatarBlink();
  }, 2600 + Math.random() * 3400);
}

function avatarWave(duration = 1600) {
  if (!dom.avatarRoot) {
    return;
  }
  dom.avatarRoot.classList.add("is-waving");
  if (avatarState.waveTimer !== null) {
    window.clearTimeout(avatarState.waveTimer);
  }
  avatarState.waveTimer = window.setTimeout(() => {
    dom.avatarRoot.classList.remove("is-waving");
  }, duration);
}

function clampAvatar(value, min = -1, max = 1) {
  return Math.max(min, Math.min(max, value));
}

function springAvatarValue(current, target, velocity, stiffness, damping) {
  const force = (target - current) * stiffness;
  const nextVelocity = (velocity + force) * damping;
  return {
    value: current + nextVelocity,
    velocity: nextVelocity,
  };
}

function setAvatarLayerTransform(layer, nx, ny) {
  const tx = nx * layer.x * avatarState.intensity;
  const ty = ny * layer.y * avatarState.intensity;
  const sx = springAvatarValue(layer.sx, tx, layer.svx, layer.lag, 0.78);
  const sy = springAvatarValue(layer.sy, ty, layer.svy, layer.lag, 0.78);
  layer.sx = sx.value;
  layer.sy = sy.value;
  layer.svx = sx.velocity;
  layer.svy = sy.velocity;

  const rx = -ny * layer.rx * avatarState.tilt;
  const ry = nx * layer.ry * avatarState.tilt;
  const rz = nx * layer.rz * avatarState.intensity;
  const scale = layer.scale + (Math.abs(nx) + Math.abs(ny)) * 0.0015 * avatarState.intensity;

  layer.el.style.transform = [
    "translate3d(" + layer.sx.toFixed(3) + "px," + layer.sy.toFixed(3) + "px," + layer.z + "px)",
    "rotateX(" + rx.toFixed(3) + "deg)",
    "rotateY(" + ry.toFixed(3) + "deg)",
    "rotateZ(" + rz.toFixed(3) + "deg)",
    "scale(" + scale.toFixed(5) + ")",
  ].join(" ");
}

function updateAvatarVolume(nx, ny) {
  const target = dom.avatarModel || dom.avatarRoot;
  if (!target) {
    return;
  }
  target.style.setProperty("--shade-x", (58 - nx * 9).toFixed(2) + "%");
  target.style.setProperty("--shade-y", (37 - ny * 6).toFixed(2) + "%");
  target.style.setProperty("--shade2-x", (47 - nx * 5).toFixed(2) + "%");
  target.style.setProperty("--shade2-y", (56 - ny * 5).toFixed(2) + "%");
  target.style.setProperty("--light-x", (42 + nx * 8).toFixed(2) + "%");
  target.style.setProperty("--light-y", (25 + ny * 5).toFixed(2) + "%");
  target.style.setProperty("--neck-x", (50 + nx * 3).toFixed(2) + "%");
  target.style.setProperty("--shade-opacity", (0.09 + (Math.abs(nx) + Math.abs(ny)) * 0.04).toFixed(3));
  target.style.setProperty("--light-opacity", (0.12 + (Math.abs(nx) + Math.abs(ny)) * 0.05).toFixed(3));
  target.style.setProperty("--neck-opacity", (0.10 + Math.abs(ny) * 0.05).toFixed(3));
  target.style.setProperty("--base-shadow-x", (-nx * 3.0).toFixed(2) + "px");
  target.style.setProperty("--base-shadow-y", (2 - ny * 1.4).toFixed(2) + "px");
  target.style.setProperty("--layer-shadow-x", (-nx * 2.2).toFixed(2) + "px");
  target.style.setProperty("--layer-shadow-y", (1.8 - ny * 1.2).toFixed(2) + "px");
  target.style.setProperty("--face-shadow-x", (-nx * 0.9).toFixed(2) + "px");
  target.style.setProperty("--face-shadow-y", (0.8 - ny * 0.5).toFixed(2) + "px");
  dom.avatarRoot.style.setProperty("--blink-x", (nx * 8).toFixed(2) + "px");
  dom.avatarRoot.style.setProperty("--blink-y", (ny * 4.6).toFixed(2) + "px");
}

function animateAvatarFrame(time) {
  if (!dom.avatarRoot) {
    return;
  }
  const nextX = springAvatarValue(avatarState.currentX, avatarState.targetX, avatarState.velocityX, 0.095, 0.80);
  const nextY = springAvatarValue(avatarState.currentY, avatarState.targetY, avatarState.velocityY, 0.095, 0.80);
  avatarState.currentX = nextX.value;
  avatarState.currentY = nextY.value;
  avatarState.velocityX = nextX.velocity;
  avatarState.velocityY = nextY.velocity;

  const nx = clampAvatar(avatarState.currentX);
  const ny = clampAvatar(avatarState.currentY);
  const rootX = nx * 1.3 * avatarState.intensity;
  const rootY = ny * 0.9 * avatarState.intensity;
  const rootRx = -ny * 1.4 * avatarState.tilt;
  const rootRy = nx * 1.8 * avatarState.tilt;
  const rootRz = nx * 0.14 * avatarState.intensity;
  const glow = avatarState.talking ? 0.52 : 0.18;

  dom.avatarRoot.style.transform = [
    "translate3d(" + rootX.toFixed(3) + "px," + rootY.toFixed(3) + "px,0)",
    "rotateX(" + rootRx.toFixed(3) + "deg)",
    "rotateY(" + rootRy.toFixed(3) + "deg)",
    "rotateZ(" + rootRz.toFixed(3) + "deg)",
  ].join(" ");

  if (dom.avatarShell) {
    dom.avatarShell.style.setProperty("--avatar-glow", glow.toFixed(3));
  }
  for (const layer of avatarRigLayers) {
    setAvatarLayerTransform(layer, nx, ny);
  }
  updateAvatarVolume(nx, ny);

  avatarState.rafId = window.requestAnimationFrame(animateAvatarFrame);
}

function resetAvatarTilt() {
  avatarState.targetX = 0;
  avatarState.targetY = 0;
}

function setAvatarTargetFromViewport(clientX, clientY) {
  const width = window.innerWidth || document.documentElement.clientWidth || 1;
  const height = window.innerHeight || document.documentElement.clientHeight || 1;
  const px = clampAvatar(clientX / width, 0, 1);
  const py = clampAvatar(clientY / height, 0, 1);
  avatarState.targetX = clampAvatar((px - 0.5) * 2.9);
  avatarState.targetY = clampAvatar((py - 0.5) * 2.9);
}

function initAvatarInteractions() {
  if (!dom.avatarShell || !dom.avatarRoot) {
    return;
  }
  scheduleAvatarBlink();
  if (avatarState.rafId === null) {
    avatarState.rafId = window.requestAnimationFrame(animateAvatarFrame);
  }
  window.addEventListener("pointermove", (event) => {
    setAvatarTargetFromViewport(event.clientX, event.clientY);
  }, { passive: true });
  window.addEventListener("pointerout", (event) => {
    if (!event.relatedTarget) {
      resetAvatarTilt();
    }
  });
  document.addEventListener("pointerleave", resetAvatarTilt);
  window.addEventListener("blur", resetAvatarTilt);
}

function buildApiCandidates() {
  const candidates = [];
  if (location.protocol.startsWith("http")) {
    candidates.push(location.origin);
  }
  if (location.protocol !== "https:" && location.origin !== "http://127.0.0.1:8799") {
    candidates.push("http://127.0.0.1:8799");
  }
  if (location.protocol === "file:") {
    candidates.push("http://127.0.0.1:8799");
  }
  return [...new Set(candidates)];
}

async function connectApi() {
  const candidates = buildApiCandidates();
  setApiBootWaiting("Checking API connection...");
  for (const [index, candidate] of candidates.entries()) {
    try {
      setApiBootWaiting("Checking " + candidate + "...");
      const response = await fetch(candidate + "/api/control-page/state", { cache: "no-store" });
      if (!response.ok) {
        continue;
      }
      const payload = await response.json();
      state.apiBase = candidate;
      applyBootProgressPayload(payload);
      return payload;
    } catch (_error) {
      // try next candidate
    }
  }
  state.apiBase = null;
  setApiBootWaiting("Waiting for API response...");
  return null;
}

async function fetchApi(path, options = {}) {
  if (!state.apiBase) {
    const payload = await connectApi();
    if (!payload) {
      throw new Error("api_unavailable");
    }
    if (path === "/api/control-page/state" && (!options.method || options.method === "GET")) {
      return payload;
    }
  }
  const response = await fetch(state.apiBase + path, {
    cache: "no-store",
    ...options,
  });
  if (!response.ok) {
    throw new Error("api_error:" + response.status);
  }
  return await response.json();
}

function openWallpaperDatabase() {
  return new Promise((resolve, reject) => {
    if (!window.indexedDB) {
      reject(new Error("indexeddb_unavailable"));
      return;
    }
    const request = window.indexedDB.open(WALLPAPER_DB_NAME, WALLPAPER_DB_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(WALLPAPER_STORE_NAME)) {
        database.createObjectStore(WALLPAPER_STORE_NAME);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("indexeddb_open_failed"));
  });
}

async function readStoredWallpaper() {
  const database = await openWallpaperDatabase();
  return await new Promise((resolve, reject) => {
    const transaction = database.transaction(WALLPAPER_STORE_NAME, "readonly");
    const request = transaction.objectStore(WALLPAPER_STORE_NAME).get(WALLPAPER_KEY);
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => reject(request.error || new Error("wallpaper_read_failed"));
    transaction.oncomplete = () => database.close();
    transaction.onerror = () => {
      database.close();
      reject(transaction.error || new Error("wallpaper_transaction_failed"));
    };
  });
}

async function storeWallpaper(blob) {
  const database = await openWallpaperDatabase();
  return await new Promise((resolve, reject) => {
    const transaction = database.transaction(WALLPAPER_STORE_NAME, "readwrite");
    transaction.objectStore(WALLPAPER_STORE_NAME).put(blob, WALLPAPER_KEY);
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => {
      database.close();
      reject(transaction.error || new Error("wallpaper_write_failed"));
    };
  });
}

function applyWallpaperBlob(blob) {
  if (!dom.controlPageRoot || !blob) {
    return;
  }
  if (state.wallpaperObjectUrl) {
    URL.revokeObjectURL(state.wallpaperObjectUrl);
  }
  state.wallpaperObjectUrl = URL.createObjectURL(blob);
  dom.controlPageRoot.style.setProperty("--control-wallpaper-image", 'url("' + state.wallpaperObjectUrl + '")');
  dom.controlPageRoot.classList.add("has-custom-wallpaper");
}

async function restoreWallpaper() {
  try {
    const blob = await readStoredWallpaper();
    if (blob) {
      applyWallpaperBlob(blob);
    }
  } catch (error) {
    console.warn("Control page wallpaper restore failed.", error);
  }
}

function initWallpaperPicker() {
  if (!dom.controlWallpaperButton || !dom.controlWallpaperInput) {
    return;
  }
  dom.controlWallpaperButton.addEventListener("click", () => {
    dom.controlWallpaperInput.click();
  });
  dom.controlWallpaperInput.addEventListener("change", async () => {
    const file = dom.controlWallpaperInput.files && dom.controlWallpaperInput.files[0];
    dom.controlWallpaperInput.value = "";
    if (!file || !String(file.type || "").startsWith("image/")) {
      return;
    }
    applyWallpaperBlob(file);
    try {
      await storeWallpaper(file);
    } catch (error) {
      console.warn("Control page wallpaper save failed.", error);
    }
  });
  restoreWallpaper();
}

async function requestBootSplashShutdown() {
  if (!dom.bootSplashShutdownButton || dom.bootSplashShutdownButton.disabled) {
    return;
  }
  dom.bootSplashShutdownButton.disabled = true;
  if (dom.bootSplashShutdownStatus) {
    dom.bootSplashShutdownStatus.textContent = "Shutdown requested...";
  }
  try {
    const payload = await fetchApi("/api/control-page/shutdown", { method: "POST" });
    if (dom.bootSplashShutdownStatus) {
      dom.bootSplashShutdownStatus.textContent = payload?.message || "Shutdown is running.";
    }
  } catch (error) {
    if (dom.bootSplashShutdownStatus) {
      dom.bootSplashShutdownStatus.textContent = "Shutdown failed: " + error.message;
    }
    dom.bootSplashShutdownButton.disabled = false;
  }
}

function commandSuggestionsForInput(rawValue) {
  const value = rawValue.trim().toLowerCase();
  if (!value.startsWith("/")) {
    return [];
  }
  const commands = mergedCommandCatalog(CONTROL_PAGE_COMMAND_CATALOG, state.commands, state.allCommands);
  if (value === "/") {
    return commands;
  }
  return commands.filter((item) => {
    const haystack = (item.command + " " + item.summary).toLowerCase();
    return haystack.includes(value);
  });
}

function isStaleControlCommand(item) {
  const command = String((item && item.command) || "").toLowerCase();
  const template = String((item && item.template) || "").toLowerCase();
  return (
    command.startsWith("/ui")
    || command.startsWith("/windows")
    || command.startsWith("/show")
    || template.startsWith("/ui")
    || template.startsWith("/windows")
    || template.startsWith("/show")
  );
}

function mergedCommandCatalog(...groups) {
  const byCommand = new Map();
  for (const group of groups) {
    if (!Array.isArray(group)) {
      continue;
    }
    for (const item of group) {
      if (!item || !item.command || isStaleControlCommand(item)) {
        continue;
      }
      const command = String(item.command);
      const key = command.toLowerCase();
      if (byCommand.has(key)) {
        const previous = byCommand.get(key);
        byCommand.set(key, {
          ...item,
          template: item.template || previous.template || command,
          summary: item.summary || previous.summary || "",
        });
        continue;
      }
      byCommand.set(key, {
        command,
        template: item.template || command,
        summary: item.summary || "",
      });
    }
  }
  return Array.from(byCommand.values());
}

function commandHelpGroup(item) {
  const command = String((item && item.command) || "").toLowerCase();
  if (command.startsWith("/voice")) return "Voice";
  if (command === "/memory" || command === "/obsidian") return "Memory";
  if (command.startsWith("/minecraft") || command === "/inventory" || command.startsWith("/voyager")) return "Minecraft";
  if (command.startsWith("/autonomy")) return "Autonomy";
  if (command === "/shutdown") return "Danger";
  return "General";
}

function formatCommandHelp(commands) {
  const catalog = mergedCommandCatalog(CONTROL_PAGE_COMMAND_CATALOG, commands || []);
  const lines = ["Available commands"];
  for (const group of ["General", "Memory", "Voice", "Minecraft", "Autonomy", "Danger"]) {
    const items = catalog.filter((item) => commandHelpGroup(item) === group);
    if (!items.length) {
      continue;
    }
    lines.push("", group);
    for (const item of items) {
      lines.push("- " + item.command + " - " + item.summary);
    }
  }
  return lines.join("\n");
}

function renderSuggestions() {
  if (!dom.commandSuggestions || !dom.commandInput) {
    return;
  }
  state.suggestionItems = commandSuggestionsForInput(dom.commandInput.value);
  if (!state.suggestionItems.length) {
    dom.commandSuggestions.classList.add("is-hidden");
    dom.commandSuggestions.innerHTML = "";
    return;
  }
  state.selectedSuggestionIndex = Math.max(0, Math.min(state.selectedSuggestionIndex, state.suggestionItems.length - 1));
  dom.commandSuggestions.classList.remove("is-hidden");
  dom.commandSuggestions.innerHTML = state.suggestionItems.map((item, index) => {
    const selected = index === state.selectedSuggestionIndex ? " is-selected" : "";
    return (
      '<button type="button" class="command-suggestion' + selected + '" data-suggestion-index="' + index + '" title="' + escapeHtml(item.summary || item.command) + '">' +
        '<strong>' + escapeHtml(item.command) + "</strong>" +
        "<span>" + escapeHtml(item.summary) + "</span>" +
      "</button>"
    );
  }).join("");
}

function applySuggestion(index, submit = false) {
  const item = state.suggestionItems[index];
  if (!item || !dom.commandInput) {
    return;
  }
  dom.commandInput.value = item.template || item.command;
  dom.commandInput.focus();
  autosizeTextarea();
  renderSuggestions();
  if (submit) {
    sendCurrentMessage(dom.commandInput.value);
  }
}

function formatDistance(blocks) {
  if (typeof blocks !== "number" || Number.isNaN(blocks)) {
    return "-";
  }
  if (blocks >= 1000) {
    return (blocks / 1000).toFixed(1) + " km";
  }
  return blocks.toFixed(0) + " blk";
}

function formatHealthHunger(health, hunger) {
  const parts = [];
  if (typeof health === "number") {
    parts.push("HP " + health);
  }
  if (typeof hunger === "number") {
    parts.push("Food " + hunger);
  }
  return parts.length ? parts.join(" / ") : "-";
}

function renderActivityRows(items) {
  if (!dom.recentActivityList) {
    return;
  }
  if (!items || !items.length) {
    dom.recentActivityList.innerHTML = [
      '<div class="event-row empty-row">',
      "<span>--:--</span>",
      "<strong>No recent activity yet.</strong>",
      "<em>WAIT</em>",
      "</div>",
    ].join("");
    return;
  }
  const visibleItems = items.slice(0, 3);
  dom.recentActivityList.innerHTML = visibleItems.map((item, index) => {
    const kind = item.kind === "failed"
      ? "FAIL"
      : (item.kind === "live" ? "LIVE" : (item.kind === "waiting" ? "WAIT" : "DONE"));
    return [
      '<div class="event-row">',
      "<span>" + String(index + 1).padStart(2, "0") + "</span>",
      "<strong>" + escapeHtml(item.label || "") + "</strong>",
      "<em>" + escapeHtml(item.detail || kind) + "</em>",
      "</div>",
    ].join("");
  }).join("");
}

function meterLevel(state) {
  switch (state) {
    case "active":
      return 100;
    case "warm":
      return 68;
    case "standby":
      return 42;
    default:
      return 16;
  }
}

function defaultQuickCommands(minecraftActive) {
  return minecraftActive
    ? [
        { command: "/inventory", template: "/inventory", summary: "Show Minecraft inventory" },
        { command: "/minecraft status", template: "/minecraft status", summary: "Show Minecraft status and current task" },
        { command: "/minecraft disconnect", template: "/minecraft disconnect", summary: "Stop Voyager Minecraft mode" },
        { command: "/shutdown", template: "/shutdown", summary: "Shut down Evelyn runtime" },
        { command: "/help", template: "/help", summary: "Show available commands" },
      ]
    : [
        { command: "/minecraft connect", template: "/minecraft connect", summary: "Start Voyager Minecraft mode" },
        { command: "/status", template: "/status", summary: "Show Evelyn, voice, and Minecraft status" },
        { command: "/autonomy status", template: "/autonomy status", summary: "Show autonomy status" },
        { command: "/shutdown", template: "/shutdown", summary: "Shut down Evelyn runtime" },
        { command: "/help", template: "/help", summary: "Show available commands" },
      ];
}

function renderQuickCommands() {
  if (!dom.quickCommandRow && !dom.primaryActionRow) {
    return;
  }
  const commands = state.commands.length ? state.commands : [];
  const payload = state.appState || {};
  const minecraftActive = Boolean(payload.minecraft && payload.minecraft.sessionActive);
  const sourceCommands = commands.length ? commands : defaultQuickCommands(minecraftActive);
  const primaryActions = buildPrimaryActions(payload, sourceCommands);
  const supportActions = buildSupportActions(payload, sourceCommands, primaryActions);
  const repairAction = runtimeRepairActionFromPayload(payload);
  if (repairAction) {
    const hasRepair = supportActions.some((item) => item && item.repairPreview && item.actionId === repairAction.actionId && item.serviceId === repairAction.serviceId);
    if (!hasRepair) {
      supportActions.unshift(repairAction);
    }
  }
  if (dom.primaryActionRow) {
    dom.primaryActionRow.innerHTML = commandButtonMarkup(primaryActions);
  }
  if (dom.quickCommandRow) {
    dom.quickCommandRow.innerHTML = commandButtonMarkup(supportActions.slice(0, 4));
  }
}

function handleChatCommandTrigger(button) {
  if (!button) {
    return;
  }
  const command = button.getAttribute("data-chat-command") || "";
  if (!dom.commandInput) {
    return;
  }
  dom.commandInput.value = command;
  dom.commandInput.focus();
  autosizeTextarea();
  state.selectedSuggestionIndex = 0;
  renderSuggestions();
}

function applyRuntimeRepairPreview(plan, button = null) {
  state.runtimeRepairPreview = plan || null;
  const ok = Boolean(plan && plan.ok);
  const text = ok
    ? (plan.confirmToken
       ? "Ready to start: " + cleanDisplayText(plan.label, "service") + " -> " + cleanDisplayText(plan.commandText, "No command preview available")
       : "Preview only: " + cleanDisplayText(plan.message, "No repair command is required."))
    : "Repair preview failed: " + cleanDisplayText((plan && (plan.message || plan.error)) || "", "Unknown error");
  if (button && ok && plan.confirmToken) {
    button.dataset.runtimeRepairApply = "1";
    button.dataset.repairConfirmToken = plan.confirmToken;
    button.textContent = "Start: " + cleanDisplayText(plan.label, "service");
    button.classList.add("is-repair-apply");
    button.title = cleanDisplayText(plan.label, "service") + " repair confirmation";
  }
  if (dom.operatorRuntimeNote) {
    dom.operatorRuntimeNote.textContent = text;
  }
  if (dom.topbarStatusLine) {
    dom.topbarStatusLine.textContent = text;
  }
  if (dom.systemSummaryPill) {
    dom.systemSummaryPill.textContent = ok ? "Ready" : "Preview failed";
    dom.systemSummaryPill.title = text;
  }
}

async function requestRuntimeRepairPreview(button) {
  if (!button || state.runtimeRepairPreviewBusy) {
    return;
  }
  const actionId = button.getAttribute("data-repair-action-id") || "";
  const serviceId = button.getAttribute("data-repair-service-id") || "";
  state.runtimeRepairPreviewBusy = true;
  button.disabled = true;
  const previousText = button.textContent;
  button.textContent = "Previewing...";
  try {
    const plan = await fetchApi("/api/control-page/runtime-repair/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actionId, serviceId, dryRun: true }),
    });
    applyRuntimeRepairPreview(plan, button);
  } catch (error) {
    applyRuntimeRepairPreview({
      ok: false,
      error: "preview_request_failed",
      message: error.message,
    });
  } finally {
    state.runtimeRepairPreviewBusy = false;
    button.disabled = false;
    if (!button.dataset.runtimeRepairApply) {
      button.textContent = previousText || "Preview repair";
    }
  }
}

async function requestRuntimeRepairApply(button) {
  if (!button || state.runtimeRepairApplyBusy) {
    return;
  }
  const actionId = button.getAttribute("data-repair-action-id") || "";
  const serviceId = button.getAttribute("data-repair-service-id") || "";
  const confirmToken = button.getAttribute("data-repair-confirm-token") || "";
  if (!actionId || !serviceId || !confirmToken) {
    applyRuntimeRepairPreview({ ok: false, message: "Missing repair confirmation data." });
    return;
  }
  if (!window.confirm("Start repair for " + serviceId + "?")) {
    return;
  }
  state.runtimeRepairApplyBusy = true;
  button.disabled = true;
  const previousText = button.textContent;
  button.textContent = "Starting...";
  try {
    const result = await fetchApi("/api/control-page/runtime-repair/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        actionId,
        serviceId,
        confirmToken,
        reason: "operator confirmed runtime repair from Control-Page",
      }),
    });
    const text = cleanDisplayText(result.message, "Repair command was requested.");
    if (dom.operatorRuntimeNote) dom.operatorRuntimeNote.textContent = text;
    if (dom.topbarStatusLine) dom.topbarStatusLine.textContent = text;
    if (dom.systemSummaryPill) {
      dom.systemSummaryPill.textContent = result.ok ? "Repair started" : "Repair failed";
      dom.systemSummaryPill.title = text;
    }
    setTimeout(() => refreshState({ silent: true }), 1200);
  } catch (error) {
    const text = "Repair failed: " + error.message;
    if (dom.operatorRuntimeNote) dom.operatorRuntimeNote.textContent = text;
    if (dom.topbarStatusLine) dom.topbarStatusLine.textContent = text;
  } finally {
    state.runtimeRepairApplyBusy = false;
    button.disabled = false;
    button.textContent = previousText || "Start repair";
  }
}

function handleQuickActionClick(event) {
  const applyButton = event.target.closest("[data-runtime-repair-apply]");
  if (applyButton) {
    requestRuntimeRepairApply(applyButton);
    return;
  }
  const repairButton = event.target.closest("[data-runtime-repair-preview]");
  if (repairButton) {
    requestRuntimeRepairPreview(repairButton);
    return;
  }
  const commandButton = event.target.closest("[data-chat-command]");
  if (commandButton) {
    handleChatCommandTrigger(commandButton);
  }
}
function normalizeDisplayAuthor(author, role) {
  const fallback = role === "user" ? "정훈" : "Evelyn";
  const value = String(author || "").trim();
  if (!value) return fallback;
  if (/[?�ìíîïð뺥썕툝]/.test(value)) return fallback;
  return value;
}

function chatSignature(messages) {
  return JSON.stringify(messages.map((row) => [
    row.role,
    row.author,
    row.text,
    row.at,
  ]));
}

function isChatScrolledNearBottom() {
  if (!dom.chatThread) return false;
  return (dom.chatThread.scrollHeight - dom.chatThread.scrollTop - dom.chatThread.clientHeight) < 28;
}

function showNewChatMessageNotice() {
  if (!dom.chatNewMessageButton || !dom.chatNewMessageRow) return;
  dom.chatNewMessageRow.hidden = false;
}

function hideNewChatMessageNotice() {
  if (!dom.chatNewMessageRow) return;
  dom.chatNewMessageRow.hidden = true;
}

function renderChat(messages, systemText, { preserveScroll = false } = {}) {
  if (!dom.chatThread) {
    return;
  }
  const previousScrollTop = dom.chatThread.scrollTop;
  const previousScrollHeight = dom.chatThread.scrollHeight;
  const previousClientHeight = dom.chatThread.clientHeight;
  const wasNearBottom = (previousScrollHeight - (previousScrollTop + previousClientHeight)) < 28;
  const rows = Array.isArray(messages)
    ? messages.filter((row) => !isTechnicalStatusChatRow(row))
    : [];
  const signature = chatSignature(rows);
  if (signature === state.renderedChatSignature) {
    return;
  }
  const previousMessageCount = state.renderedChatMessageCount;
  const hasNewMessages = rows.length > previousMessageCount;
  dom.chatThread.innerHTML = rows.map((row) => {
    const role = row.role === "user" ? "user" : "assistant";
    const avatar = role === "assistant" ? "E" : "J";
    return [
      '<article class="chat-message" data-role="' + role + '">',
      '<div class="chat-avatar">' + avatar + "</div>",
      '<div class="chat-bubble">',
      '<div class="chat-meta">',
      "<strong>" + escapeHtml(normalizeDisplayAuthor(row.author, role)) + "</strong>",
      "<span>" + escapeHtml(formatTimestamp(row.at)) + "</span>",
      "</div>",
      "<p>" + escapeHtml(row.text || "") + "</p>",
      "</div>",
      "</article>",
    ].join("");
  }).join("");
  if (!preserveScroll || wasNearBottom) {
    dom.chatThread.scrollTop = dom.chatThread.scrollHeight;
    state.renderedChatSignature = signature;
    state.renderedChatMessageCount = rows.length;
    hideNewChatMessageNotice();
    return;
  }
  const nextScrollHeight = dom.chatThread.scrollHeight;
  const delta = nextScrollHeight - previousScrollHeight;
  dom.chatThread.scrollTop = Math.max(0, previousScrollTop + delta);
  state.renderedChatSignature = signature;
  state.renderedChatMessageCount = rows.length;
  if (previousMessageCount > 0 && hasNewMessages) {
    showNewChatMessageNotice();
  }
}

const MEMORY_GRAPH_COLORS = {
  core: "#fff8ea",
  project: "#fff8ea",
  episode: "#fff8ea",
  concept: "#fff8ea",
  procedure: "#fff8ea",
  daily: "#fff8ea",
  note: "#fff8ea",
};
const MEMORY_GRAPH_RINGS = { core: 0.12, project: 0.28, procedure: 0.44, concept: 0.58, episode: 0.72, daily: 0.86, note: 0.66 };
function memoryGraphTypeColor(type) {
  return MEMORY_GRAPH_COLORS[String(type || "note").toLowerCase()] || MEMORY_GRAPH_COLORS.note;
}

function compactMemoryGraphDate(value) {
  const text = String(value || "");
  const match = text.match(/(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})/);
  if (!match) {
    return null;
  }
  return {
    year: match[1],
    month: match[2],
    day: match[3],
  };
}

function formatMemoryGraphDate(date, format = "compact") {
  if (!date) {
    return "";
  }
  if (format === "daily") {
    return date.year.slice(-2) + "." + date.day;
  }
  return date.month + "." + date.day;
}

function memoryGraphNodeLabel(node) {
  const type = String(node?.type || node?.category || "note").toLowerCase();
  const date = compactMemoryGraphDate([
    node?.updated_at,
    node?.created_at,
    node?.rel_path,
    node?.path,
    node?.id,
    node?.title,
  ].filter(Boolean).join(" "));
  if (type === "legacy") return "";
  if (type === "daily") return formatMemoryGraphDate(date, "daily") || "daily";
  if (type === "core") return "core";
  if (type === "project") return "Project";
  return formatMemoryGraphDate(date) || type || "note";
}

function memoryGraphVisiblePayload() {
  const payload = state.memoryGraphPayload || { nodes: [], edges: [] };
  const nodes = Array.isArray(payload.nodes) ? payload.nodes : [];
  const edges = Array.isArray(payload.edges) ? payload.edges : [];
  const filterType = state.memoryGraphFilterType || "all";
  const visibleNodes = filterType === "all" ? nodes : nodes.filter((node) => node.type === filterType);
  const visibleIds = new Set(visibleNodes.map((node) => node.id));
  return {
    ...payload,
    nodes: visibleNodes,
    edges: edges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
  };
}

function memoryGraphCanvasSize() {
  const canvas = dom.memoryGraphCanvas;
  const rect = canvas?.getBoundingClientRect?.();
  return {
    width: Math.max(360, Math.floor(rect?.width || canvas?.clientWidth || 760)),
    height: Math.max(360, Math.floor(rect?.height || canvas?.clientHeight || 460)),
  };
}

function memoryGraphClampPoint(x, y) {
  const { width, height } = memoryGraphCanvasSize();
  return {
    x: Math.max(24, Math.min(width - 24, x)),
    y: Math.max(24, Math.min(height - 24, y)),
  };
}

function memoryGraphLocalPoint(clientX, clientY) {
  const canvas = dom.memoryGraphCanvas;
  const rect = canvas?.getBoundingClientRect?.();
  const { width, height } = memoryGraphCanvasSize();
  if (!rect || rect.width <= 0 || rect.height <= 0) {
    return memoryGraphClampPoint(width / 2, height / 2);
  }
  return {
    x: ((clientX - rect.left) / rect.width) * width,
    y: ((clientY - rect.top) / rect.height) * height,
  };
}

function memoryGraphNodeAnchor(type, index, total, width, height) {
  const normalizedType = String(type || "note").toLowerCase();
  const ring = MEMORY_GRAPH_RINGS[normalizedType] || MEMORY_GRAPH_RINGS.note;
  const angle = ((index / Math.max(1, total)) * Math.PI * 2) + (normalizedType.length * 0.31);
  const radius = Math.min(width, height) * ring * 0.5;
  return {
    x: (width / 2) + Math.cos(angle) * radius,
    y: (height / 2) + Math.sin(angle) * radius,
  };
}

function prepareMemoryGraphLayout(payload) {
  if (!payload || !Array.isArray(payload.nodes)) {
    return payload;
  }
  const { width, height } = memoryGraphCanvasSize();
  const centerX = width / 2;
  const centerY = height / 2;
  const previousWidth = Number(payload._layoutWidth || 0);
  const previousHeight = Number(payload._layoutHeight || 0);
  const shouldScaleExisting = previousWidth > 0
    && previousHeight > 0
    && (Math.abs(previousWidth - width) > 2 || Math.abs(previousHeight - height) > 2);
  const previousCenterX = previousWidth / 2;
  const previousCenterY = previousHeight / 2;
  const scaleX = previousWidth > 0 ? width / previousWidth : 1;
  const scaleY = previousHeight > 0 ? height / previousHeight : 1;
  const counts = {};
  for (const node of payload.nodes) {
    const type = node.type || "note";
    counts[type] = (counts[type] || 0) + 1;
  }
  const seen = {};
  for (const node of payload.nodes) {
    const type = node.type || "note";
    const index = seen[type] || 0;
    seen[type] = index + 1;
    const total = Math.max(1, counts[type] || 1);
    const anchor = memoryGraphNodeAnchor(type, index, total, width, height);
    node.anchorX = anchor.x;
    node.anchorY = anchor.y;
    if (shouldScaleExisting && Number.isFinite(node.x) && Number.isFinite(node.y)) {
      node.x = centerX + (node.x - previousCenterX) * scaleX;
      node.y = centerY + (node.y - previousCenterY) * scaleY;
    } else {
      node.x = Number.isFinite(node.x) ? node.x : node.anchorX;
      node.y = Number.isFinite(node.y) ? node.y : node.anchorY;
    }
    node.x = Math.max(30, Math.min(width - 30, node.x));
    node.y = Math.max(30, Math.min(height - 30, node.y));
    node.vx = Number.isFinite(node.vx) ? node.vx : 0;
    node.vy = Number.isFinite(node.vy) ? node.vy : 0;
  }
  payload._layoutWidth = width;
  payload._layoutHeight = height;
  return payload;
}

function stepMemoryGraphSimulation(payload) {
  if (!payload || !payload.nodes || !payload.nodes.length) {
    return;
  }
  const { width, height } = memoryGraphCanvasSize();
  const nodeById = new Map(payload.nodes.map((node) => [node.id, node]));
  for (const node of payload.nodes) {
    const anchorX = Number.isFinite(node.anchorX) ? node.anchorX : width / 2;
    const anchorY = Number.isFinite(node.anchorY) ? node.anchorY : height / 2;
    node.vx += (anchorX - node.x) * 0.0012;
    node.vy += (anchorY - node.y) * 0.0012;
  }
  for (let i = 0; i < payload.nodes.length; i += 1) {
    const left = payload.nodes[i];
    for (let j = i + 1; j < payload.nodes.length; j += 1) {
      const right = payload.nodes[j];
      const dx = right.x - left.x;
      const dy = right.y - left.y;
      const dist2 = Math.max(80, dx * dx + dy * dy);
      const force = Math.min(0.7, 460 / dist2);
      const dist = Math.sqrt(dist2);
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      left.vx -= fx;
      left.vy -= fy;
      right.vx += fx;
      right.vy += fy;
    }
  }
  for (const edge of payload.edges || []) {
    const source = nodeById.get(edge.source);
    const target = nodeById.get(edge.target);
    if (!source || !target) {
      continue;
    }
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy));
    const desired = 86 + Math.max(0, 9 - Number(edge.weight || 0) * 10);
    const force = (dist - desired) * 0.0028 * Math.min(2.2, Math.max(0.4, Number(edge.weight || 1)));
    const fx = (dx / dist) * force;
    const fy = (dy / dist) * force;
    source.vx += fx;
    source.vy += fy;
    target.vx -= fx;
    target.vy -= fy;
  }
  for (const node of payload.nodes) {
    if (state.memoryGraphPointer.dragId === node.id || state.memoryGraphPointer.holdId === node.id) {
      const point = {
        x: state.memoryGraphPointer.x,
        y: state.memoryGraphPointer.y,
      };
      if (state.memoryGraphPointer.dragId === node.id) {
        node.x = point.x;
        node.y = point.y;
      }
      node.vx = 0;
      node.vy = 0;
      continue;
    }
    node.vx *= 0.86;
    node.vy *= 0.86;
    node.x = Math.max(-160, Math.min(width + 160, node.x + node.vx));
    node.y = Math.max(-160, Math.min(height + 160, node.y + node.vy));
  }
}

function drawMemoryGraph(payload) {
  const canvas = dom.memoryGraphCanvas;
  if (!canvas) {
    return;
  }
  const scale = window.devicePixelRatio || 1;
  const { width, height } = memoryGraphCanvasSize();
  if (canvas.width !== Math.floor(width * scale) || canvas.height !== Math.floor(height * scale)) {
    canvas.width = Math.floor(width * scale);
    canvas.height = Math.floor(height * scale);
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "rgba(5, 10, 12, 0.72)";
  ctx.fillRect(0, 0, width, height);
  const nodeById = new Map((payload.nodes || []).map((node) => [node.id, node]));
  for (const edge of payload.edges || []) {
    const source = nodeById.get(edge.source);
    const target = nodeById.get(edge.target);
    if (!source || !target) {
      continue;
    }
    const alpha = Math.max(0.14, Math.min(0.58, 0.16 + Number(edge.weight || 0.4) * 0.16));
    ctx.beginPath();
    ctx.moveTo(source.x, source.y);
    ctx.lineTo(target.x, target.y);
    ctx.strokeStyle = edge.type === "semantic_similarity"
      ? "rgba(119, 214, 202, " + alpha + ")"
      : (edge.type === "shared_tag" ? "rgba(242, 180, 111, " + alpha + ")" : "rgba(255, 248, 234, " + alpha + ")");
    ctx.lineWidth = Math.max(0.8, Math.min(3.2, Number(edge.weight || 0.5)));
    ctx.stroke();
  }
  for (const node of payload.nodes || []) {
    const nodeScale = Math.max(0.25, Math.min(2, Number(state.memoryGraphNodeScale || 1)));
    const radius = Math.max(4, Math.min(24, Number(node.size || 14) * 0.42 * nodeScale));
    const selected = node.id === state.memoryGraphSelectedNodeId;
    const hovered = node.id === state.memoryGraphPointer.hoverId;
    ctx.beginPath();
    ctx.arc(node.x, node.y, radius + (selected ? 4 : (hovered ? 2 : 0)), 0, Math.PI * 2);
    ctx.fillStyle = selected ? "rgba(255, 248, 234, 0.20)" : (hovered ? "rgba(255, 248, 234, 0.13)" : "rgba(255, 248, 234, 0.05)");
    ctx.fill();
    ctx.beginPath();
    ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
    ctx.fillStyle = memoryGraphTypeColor(node.type);
    ctx.fill();
    ctx.lineWidth = selected ? 2.4 : 1.2;
    ctx.strokeStyle = selected ? "rgba(145, 197, 184, 0.88)" : "rgba(255, 255, 255, 0.72)";
    ctx.stroke();
    ctx.font = "600 8px IBM Plex Sans KR, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillStyle = "rgba(255, 248, 234, 0.92)";
    const label = memoryGraphNodeLabel(node);
    ctx.fillText(label, node.x, node.y + radius + 5, Math.max(42, radius * 3.4));
  }
  ctx.textAlign = "start";
  ctx.textBaseline = "alphabetic";
}

function animateMemoryGraph() {
  const payload = memoryGraphVisiblePayload();
  stepMemoryGraphSimulation(payload);
  drawMemoryGraph(payload);
  state.memoryGraphFrame = window.requestAnimationFrame(animateMemoryGraph);
}

function startMemoryGraphAnimation() {
  if (state.memoryGraphFrame !== null) {
    return;
  }
  state.memoryGraphFrame = window.requestAnimationFrame(animateMemoryGraph);
}

function nearestMemoryGraphNode(clientX, clientY) {
  if (!dom.memoryGraphCanvas) {
    return null;
  }
  const { x, y } = memoryGraphLocalPoint(clientX, clientY);
  state.memoryGraphPointer.x = x;
  state.memoryGraphPointer.y = y;
  let best = null;
  let bestDist = 999999;
  for (const node of memoryGraphVisiblePayload().nodes || []) {
    const dx = node.x - x;
    const dy = node.y - y;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const nodeScale = Math.max(0.25, Math.min(2, Number(state.memoryGraphNodeScale || 1)));
    const radius = Math.max(7, Math.min(30, Number(node.size || 14) * 0.48 * nodeScale));
    if (dist < radius && dist < bestDist) {
      best = node;
      bestDist = dist;
    }
  }
  return best;
}

function updateMemoryGraphDragPoint(event) {
  const point = memoryGraphLocalPoint(event.clientX, event.clientY);
  state.memoryGraphPointer.x = point.x - Number(state.memoryGraphPointer.offsetX || 0);
  state.memoryGraphPointer.y = point.y - Number(state.memoryGraphPointer.offsetY || 0);
}

function centerMemoryGraphNodeOnPointer(nodeId, event) {
  const point = memoryGraphLocalPoint(event.clientX, event.clientY);
  state.memoryGraphPointer.x = point.x;
  state.memoryGraphPointer.y = point.y;
  const node = (memoryGraphVisiblePayload().nodes || []).find((item) => item.id === nodeId);
  if (!node) {
    return;
  }
  node.x = point.x;
  node.y = point.y;
  node.vx = 0;
  node.vy = 0;
}

function renderMemoryGraphDetail(node) {
  if (!dom.memoryGraphDetail) {
    return;
  }
  if (!node) {
    dom.memoryGraphDetail.innerHTML = [
      "<strong>Select a memory node</strong>",
      "<p>Click a node to inspect its note path, type, tags, and connected memory reasons.</p>",
    ].join("");
    return;
  }
  const tags = Array.isArray(node.tags) && node.tags.length ? node.tags.slice(0, 8).join(", ") : "none";
  const projects = Array.isArray(node.projects) && node.projects.length ? node.projects.slice(0, 5).join(", ") : "none";
  const edges = (state.memoryGraphPayload?.edges || []).filter((edge) => edge.source === node.id || edge.target === node.id);
  const edgeSummary = edges.slice(0, 5).map((edge) => escapeHtml(edge.type + ": " + (edge.label || ""))).join("<br>") || "No visible edges";
  const locked = Boolean(node.locked || node.canEdit === false || node.contentHidden);
  if (locked) {
    dom.memoryGraphDetail.innerHTML = [
      "<strong>Archived memory</strong>",
      '<div class="memory-node-meta">',
      "<span>Status <strong>Archived</strong></span>",
      "<span>Degree <strong>" + escapeHtml(node.degree || 0) + "</strong></span>",
      "<span>Importance <strong>" + escapeHtml(Number(node.importance || 0).toFixed(2)) + "</strong></span>",
      "</div>",
      "<p>Archived memory is locked. Contents are hidden in public views.</p>",
    ].join("");
    return;
  }
  dom.memoryGraphDetail.innerHTML = [
    "<strong>" + escapeHtml(node.title || node.id) + "</strong>",
    '<p class="memory-node-path">' + escapeHtml(node.rel_path || "") + "</p>",
    '<div class="memory-node-meta">',
    "<span>Type <strong>" + escapeHtml(node.type || "note") + "</strong></span>",
    "<span>Degree <strong>" + escapeHtml(node.degree || 0) + "</strong></span>",
    "<span>Importance <strong>" + escapeHtml(Number(node.importance || 0).toFixed(2)) + "</strong></span>",
    "</div>",
    "<p><b>Tags</b> " + escapeHtml(tags) + "</p>",
    "<p><b>Projects</b> " + escapeHtml(projects) + "</p>",
    "<p><b>Edges</b><br>" + edgeSummary + "</p>",
    "<p>" + escapeHtml(node.snippet || "") + "</p>",
  ].join("");
}

function memoryCardStatusLabel(card) {
  if (card.hidden) {
    return "Hidden";
  }
  if (card.locked || card.canEdit === false || card.contentHidden) {
    return "Locked";
  }
  return card.confirmed ? "Confirmed" : "Needs review";
}

function memoryTypeDisplayLabel(type) {
  const normalized = String(type || "note").toLowerCase();
  if (normalized === "legacy") {
    return "Archived";
  }
  if (normalized === "all") {
    return "All";
  }
  return type || "note";
}

function renderMemoryCards(payload) {
  state.memoryCardsPayload = payload || { cards: [], counts: {} };
  const counts = state.memoryCardsPayload.counts || {};
  if (dom.memoryManagerSummary) {
    dom.memoryManagerSummary.innerHTML = [
      "<span>Confirmed <strong>" + escapeHtml(counts.confirmed || 0) + "</strong></span>",
      "<span>Unconfirmed <strong>" + escapeHtml(counts.unconfirmed || 0) + "</strong></span>",
      "<span>Pinned <strong>" + escapeHtml(counts.pinned || 0) + "</strong></span>",
    ].join("");
  }
  if (!dom.memoryCardList) {
    return;
  }
  const cards = Array.isArray(state.memoryCardsPayload.cards) ? state.memoryCardsPayload.cards : [];
  if (!cards.length) {
    dom.memoryCardList.innerHTML = '<article class="memory-card memory-card-empty">No memory cards are available.</article>';
    return;
  }
  dom.memoryCardList.innerHTML = cards.slice(0, 12).map((card) => {
    const statusClass = card.confirmed ? "is-confirmed" : "is-unconfirmed";
    const locked = Boolean(card.locked || card.canEdit === false || card.contentHidden);
    const pinLabel = card.pinned ? "Unpin" : "Pin";
    const pinAction = card.pinned ? "unpin" : "pin";
    const confirmLabel = card.confirmed ? "Unconfirm" : "Confirm";
    const confirmAction = card.confirmed ? "unconfirm" : "confirm";
    const preview = locked ? (card.preview || "Archived memory is locked.") : (card.preview || "No preview");
    const category = locked ? "Archived" : (card.category || card.type || "note");
    const metaPath = locked ? "Archived" : (card.path || "");
    const title = locked ? "Archived memory" : (card.title || "Untitled memory");
    return [
      '<article class="memory-card ' + statusClass + (locked ? ' is-locked' : '') + '" data-memory-note-id="' + escapeHtml(card.id) + '">',
      '<div class="memory-card-head">',
      '<div>',
      '<span class="memory-card-category">' + escapeHtml(category) + "</span>",
      "<strong>" + escapeHtml(title) + "</strong>",
      "</div>",
      '<span class="memory-card-status">' + escapeHtml(memoryCardStatusLabel(card)) + "</span>",
      "</div>",
      '<p class="memory-card-preview">' + escapeHtml(preview) + "</p>",
      '<div class="memory-card-meta">',
      "<span>" + escapeHtml(metaPath) + "</span>",
      card.pinned ? "<span>Pinned</span>" : "",
      card.confirmedAt ? "<span>" + escapeHtml(card.confirmedAt.slice(0, 10)) + "</span>" : "",
      "</div>",
      '<div class="memory-card-actions">',
      '<button type="button" data-memory-action="' + confirmAction + '">' + confirmLabel + "</button>",
      '<button type="button" data-memory-action="' + pinAction + '">' + pinLabel + "</button>",
      locked ? "" : '<button type="button" data-memory-action="edit">Edit</button>',
      '<button type="button" class="is-danger" data-memory-action="hide">Hide</button>',
      "</div>",
      "</article>",
    ].join("");
  }).join("");
}

async function loadMemoryCards({ force = false } = {}) {
  if (!dom.memoryCardList || state.memoryCardsLoading) {
    return;
  }
  const now = Date.now();
  if (!force && state.memoryCardsPayload && now - state.memoryCardsLastLoadedAt < 45000) {
    return;
  }
  state.memoryCardsLoading = true;
  if (dom.memoryManagerStatus) {
    dom.memoryManagerStatus.textContent = "Loading memory cards...";
  }
  try {
    const payload = await fetchApi("/api/control-page/memory?limit=80");
    state.memoryCardsLastLoadedAt = Date.now();
    renderMemoryCards(payload);
    if (dom.memoryManagerStatus) {
      dom.memoryManagerStatus.textContent = "Memory cards loaded.";
    }
  } catch (error) {
    if (dom.memoryManagerStatus) {
      dom.memoryManagerStatus.textContent = "Failed to load memory cards: " + error.message;
    }
  } finally {
    state.memoryCardsLoading = false;
  }
}

async function updateMemoryCardAction(noteId, action, extra = {}) {
  if (!noteId || !action) {
    return false;
  }
  if (dom.memoryManagerStatus) {
    dom.memoryManagerStatus.textContent = "Updating memory card...";
  }
  try {
    await fetchApi("/api/control-page/memory/" + encodeURIComponent(noteId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...extra }),
    });
    state.memoryCardsPayload = null;
    state.memoryGraphPayload = null;
    await loadMemoryCards({ force: true });
    await loadMemoryGraph({ force: true });
    return true;
  } catch (error) {
    if (dom.memoryManagerStatus) {
      dom.memoryManagerStatus.textContent = "Memory action failed: " + error.message;
    }
    return false;
  }
}

function findMemoryCardPayload(noteId) {
  const cards = Array.isArray(state.memoryCardsPayload?.cards) ? state.memoryCardsPayload.cards : [];
  return cards.find((card) => String(card.id || "") === String(noteId || "")) || null;
}

async function fetchMemoryCardPayload(noteId) {
  const fallback = findMemoryCardPayload(noteId);
  if (!noteId) {
    return fallback;
  }
  try {
    const payload = await fetchApi("/api/control-page/memory/" + encodeURIComponent(noteId));
    return payload?.card || fallback;
  } catch (error) {
    if (dom.memoryManagerStatus) {
      dom.memoryManagerStatus.textContent = "Failed to load memory card detail: " + error.message;
    }
    return fallback;
  }
}

function updateMemoryEditorCount(editor) {
  if (!editor || !editor.count || !editor.bodyInput) {
    return;
  }
  const length = (editor.bodyInput.value || "").trim().length;
  editor.count.textContent = length + " chars";
}

function closeMemoryCardEditor() {
  const editor = state.memoryEditor;
  if (!editor || !editor.root) {
    return;
  }
  editor.root.classList.add("is-hidden");
  editor.root.setAttribute("aria-hidden", "true");
  editor.noteId = "";
}

function ensureMemoryCardEditor() {
  if (state.memoryEditor) {
    return state.memoryEditor;
  }
  const root = document.createElement("div");
  root.className = "memory-editor-backdrop is-hidden";
  root.setAttribute("aria-hidden", "true");
  root.innerHTML = [
    '<section class="memory-editor-dialog" role="dialog" aria-modal="true" aria-labelledby="memory-editor-title">',
    '<header class="memory-editor-header">',
    '<div>',
    '<p class="memory-editor-eyebrow">MEMORY EDIT</p>',
    '<strong id="memory-editor-title">Edit Memory</strong>',
    '<span class="memory-editor-path" data-memory-editor-path></span>',
    '</div>',
    '<button type="button" class="memory-editor-icon-button" data-memory-editor-close aria-label="Close">x</button>',
    '</header>',
    '<form class="memory-editor-form" data-memory-editor-form>',
    '<label class="memory-editor-field">',
    '<span>Title</span>',
    '<input type="text" data-memory-editor-title autocomplete="off">',
    '</label>',
    '<label class="memory-editor-field">',
    '<span>Body</span>',
    '<textarea data-memory-editor-body spellcheck="false"></textarea>',
    '</label>',
    '<footer class="memory-editor-footer">',
    '<span data-memory-editor-count>0 chars</span>',
    '<div class="memory-editor-actions">',
    '<button type="button" data-memory-editor-cancel>Cancel</button>',
    '<button type="submit" class="is-primary">Save</button>',
    '</div>',
    '</footer>',
    '</form>',
    '</section>',
  ].join("");
  document.body.appendChild(root);
  const editor = {
    root,
    form: root.querySelector("[data-memory-editor-form]"),
    titleInput: root.querySelector("[data-memory-editor-title]"),
    bodyInput: root.querySelector("[data-memory-editor-body]"),
    pathLabel: root.querySelector("[data-memory-editor-path]"),
    count: root.querySelector("[data-memory-editor-count]"),
    closeButton: root.querySelector("[data-memory-editor-close]"),
    cancelButton: root.querySelector("[data-memory-editor-cancel]"),
    noteId: "",
  };
  editor.closeButton?.addEventListener("click", closeMemoryCardEditor);
  editor.cancelButton?.addEventListener("click", closeMemoryCardEditor);
  editor.bodyInput?.addEventListener("input", () => updateMemoryEditorCount(editor));
  editor.form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const noteId = editor.noteId || "";
    if (!noteId) {
      return;
    }
    const ok = await updateMemoryCardAction(noteId, "edit", {
      title: editor.titleInput?.value || "",
      body: editor.bodyInput?.value || "",
    });
    if (ok) {
      closeMemoryCardEditor();
    }
  });
  root.addEventListener("click", (event) => {
    if (event.target === root) {
      closeMemoryCardEditor();
    }
  });
  state.memoryEditor = editor;
  return editor;
}

async function openMemoryCardEditor(noteId) {
  const editor = ensureMemoryCardEditor();
  editor.noteId = noteId;
  if (editor.titleInput) {
    editor.titleInput.value = "Loading...";
  }
  if (editor.bodyInput) {
    editor.bodyInput.value = "Loading memory card...";
  }
  if (editor.pathLabel) {
    editor.pathLabel.textContent = "";
  }
  updateMemoryEditorCount(editor);
  editor.root.classList.remove("is-hidden");
  editor.root.setAttribute("aria-hidden", "false");
  const payload = await fetchMemoryCardPayload(noteId);
  if (editor.noteId !== noteId) {
    return;
  }
  const locked = Boolean(payload?.locked || payload?.canEdit === false || payload?.contentHidden);
  if (editor.titleInput) {
    editor.titleInput.value = locked ? "Archived memory" : (payload?.title || "");
    editor.titleInput.disabled = locked;
  }
  if (editor.bodyInput) {
    editor.bodyInput.value = locked ? (payload?.preview || "This archived memory is locked.") : (payload?.body || payload?.preview || "");
    editor.bodyInput.disabled = locked;
  }
  if (editor.pathLabel) {
    editor.pathLabel.textContent = locked ? "Archived" : (payload?.path || "");
  }
  const saveButton = editor.form?.querySelector('button[type="submit"]');
  if (saveButton) {
    saveButton.disabled = locked;
  }
  updateMemoryEditorCount(editor);
  window.setTimeout(() => (locked ? editor.titleInput : editor.bodyInput)?.focus(), 40);
}

function handleMemoryCardAction(button) {
  const card = button.closest("[data-memory-note-id]");
  const noteId = card ? card.getAttribute("data-memory-note-id") : "";
  const action = button.getAttribute("data-memory-action") || "";
  if (!noteId || !action) {
    return;
  }
  if (action === "hide" && !window.confirm("Hide this memory card?")) {
    return;
  }
  if (action === "edit") {
    const payload = findMemoryCardPayload(noteId);
    if (payload && (payload.locked || payload.canEdit === false || payload.contentHidden)) {
      if (dom.memoryManagerStatus) {
        dom.memoryManagerStatus.textContent = "Archived memory is locked and cannot be edited.";
      }
      return;
    }
    openMemoryCardEditor(noteId);
    return;
  }
  updateMemoryCardAction(noteId, action);
}

function setMemoryGraphNodeScale(nextScale) {
  state.memoryGraphNodeScale = Math.max(0.25, Math.min(2, Number(nextScale) || 1));
  localStorage.setItem("evelynMemoryGraphNodeScale", String(state.memoryGraphNodeScale));
  drawMemoryGraph(memoryGraphVisiblePayload());
}

function renderMemoryGraphControls(payload) {
  if (dom.memoryGraphStats) {
    const stats = payload?.stats || {};
    dom.memoryGraphStats.innerHTML = [
      "<span>Nodes <strong>" + escapeHtml(stats.node_count || 0) + "</strong></span>",
      "<span>Edges <strong>" + escapeHtml(stats.edge_count || 0) + "</strong></span>",
      "<span>Version <strong>" + escapeHtml(payload?.memory_version || 0) + "</strong></span>",
    ].join("");
  }
  if (dom.memoryGraphSubcopy && payload) {
    dom.memoryGraphSubcopy.textContent = "Markdown vault graph, " + (payload.latency_ms || 0) + "ms export.";
  }
  if (!dom.memoryGraphFilter) {
    return;
  }
  const counts = (payload?.stats || {}).type_counts || {};
  const types = ["all", ...Object.keys(counts).sort()];
  const scale = Math.max(0.25, Math.min(2, Number(state.memoryGraphNodeScale || 1)));
  const sizeControls = [
    '<button type="button" class="memory-filter-button" data-memory-node-size="down" title="Smaller nodes">-</button>',
    '<button type="button" class="memory-filter-button memory-filter-value" data-memory-node-size="reset" title="Reset node size" aria-live="polite" aria-label="Reset node scale to 1.00x">' + escapeHtml(scale.toFixed(2)) + "x</button>",
    '<button type="button" class="memory-filter-button" data-memory-node-size="up" title="Larger nodes">+</button>',
  ].join("");
  dom.memoryGraphFilter.innerHTML = sizeControls + types.map((type) => {
    const active = state.memoryGraphFilterType === type ? " is-active" : "";
    const label = memoryTypeDisplayLabel(type);
    const count = type === "all" ? (payload?.stats?.node_count || 0) : counts[type];
    return '<button type="button" class="memory-filter-button' + active + '" data-memory-filter="' + escapeHtml(type) + '">' + escapeHtml(label) + " " + escapeHtml(count) + "</button>";
  }).join("");
}

function renderMemoryGraph(payload) {
  state.memoryGraphPayload = prepareMemoryGraphLayout(payload || { nodes: [], edges: [] });
  const visible = memoryGraphVisiblePayload();
  if (dom.memoryGraphEmpty) {
    dom.memoryGraphEmpty.classList.toggle("is-hidden", Boolean(visible.nodes && visible.nodes.length));
    dom.memoryGraphEmpty.textContent = state.memoryGraphLoading ? "Memory graph data is loading." : "No memory graph nodes available.";
  }
  renderMemoryGraphControls(state.memoryGraphPayload);
  const selected = (state.memoryGraphPayload.nodes || []).find((node) => node.id === state.memoryGraphSelectedNodeId);
  renderMemoryGraphDetail(selected || null);
  drawMemoryGraph(visible);
  startMemoryGraphAnimation();
}

async function loadMemoryGraph({ force = false } = {}) {
  if (!dom.memoryGraphCanvas || state.memoryGraphLoading) {
    return;
  }
  const now = Date.now();
  if (!force && state.memoryGraphPayload && now - state.memoryGraphLastLoadedAt < 60000) {
    return;
  }
  state.memoryGraphLoading = true;
  if (dom.memoryGraphEmpty) {
    dom.memoryGraphEmpty.classList.remove("is-hidden");
    dom.memoryGraphEmpty.textContent = "Memory graph data is loading.";
  }
  try {
    const payload = await fetchApi("/api/control-page/memory-graph?max_nodes=160");
    state.memoryGraphLastLoadedAt = Date.now();
    renderMemoryGraph(payload);
  } catch (error) {
    if (dom.memoryGraphEmpty) {
      dom.memoryGraphEmpty.classList.remove("is-hidden");
      dom.memoryGraphEmpty.textContent = "Memory graph unavailable: " + error.message;
    }
  } finally {
    state.memoryGraphLoading = false;
  }
}

function isTechnicalStatusChatRow(row) {
  if (!row || row.role === "user") {
    return false;
  }
  const text = String(row.text || "");
  if (!text.includes("Evelyn status")) {
    return false;
  }
  return [
    "main_model:",
    "router_model:",
    "summary_model:",
    "stt_model:",
    "listening:",
    "tts_speaking:",
    "voyager_running:",
  ].some((token) => text.includes(token));
}

function isCompactPanelMode() {
  return window.matchMedia("(max-width: 920px)").matches;
}

function loadPanelLayout() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PANEL_LAYOUT_STORAGE_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (_error) {
    return {};
  }
}

function savePanelLayout() {
  const next = {};
  for (const definition of PANEL_DEFINITIONS) {
    const panel = state.panels[definition.id];
    if (!panel) {
      continue;
    }
    next[definition.id] = {
      visible: panel.visible !== false,
      floating: Boolean(panel.floating),
      x: Math.round(panel.x || 0),
      y: Math.round(panel.y || 0),
      width: Math.round(panel.width || 0),
      z: panel.z || 0,
    };
  }
  window.localStorage.setItem(PANEL_LAYOUT_STORAGE_KEY, JSON.stringify(next));
}

function defaultPanelPosition(index, element) {
  const rect = element.getBoundingClientRect();
  const width = Math.min(Math.max(rect.width || 360, 300), window.innerWidth - 24);
  return {
    x: Math.max(12, Math.min(32 + index * 34, window.innerWidth - width - 12)),
    y: Math.max(12, Math.min(28 + index * 42, window.innerHeight - 160)),
    width,
    height: rect.height || 260,
  };
}

function clampPanelPosition(panel) {
  const element = panel.element;
  const rect = element.getBoundingClientRect();
  const width = rect.width || panel.width || 320;
  const height = rect.height || 220;
  panel.x = Math.max(8, Math.min(panel.x || 8, Math.max(8, window.innerWidth - width - 8)));
  panel.y = Math.max(8, Math.min(panel.y || 8, Math.max(8, window.innerHeight - height - 8)));
}

function applyPanelState(panelId) {
  const panel = state.panels[panelId];
  if (!panel || !panel.element) {
    return;
  }
  const element = panel.element;
  const compact = isCompactPanelMode();
  const placeholder = panel.placeholder;
  element.classList.toggle("is-panel-hidden", panel.visible === false);
  element.setAttribute("aria-hidden", panel.visible === false ? "true" : "false");
  if (panel.visible === false) {
    if (placeholder) {
      placeholder.style.display = "none";
    }
    element.style.left = "";
    element.style.top = "";
    element.style.width = "";
    element.style.zIndex = "";
    element.classList.remove("is-floating-panel");
    return;
  }
  if (panel.floating && !compact) {
    element.classList.add("is-floating-panel");
    if (placeholder) {
      placeholder.style.display = "block";
      placeholder.style.height = Math.max(160, Math.round(panel.flowHeight || 0)) + "px";
    }
    clampPanelPosition(panel);
    element.style.left = panel.x + "px";
    element.style.top = panel.y + "px";
    element.style.width = panel.width ? panel.width + "px" : "";
    element.style.zIndex = String(panel.z || ++state.panelZIndex);
  } else {
    if (placeholder) {
      placeholder.style.display = "none";
    }
    element.classList.remove("is-floating-panel");
    element.style.left = "";
    element.style.top = "";
    element.style.width = "";
    element.style.zIndex = "";
  }
}

function renderPanelDock() {
  let dock = document.querySelector("#panel-dock");
  if (!dock && dom.controlPageRoot) {
    dock = document.createElement("div");
    dock.id = "panel-dock";
    dock.className = "panel-dock";
    dock.setAttribute("aria-label", "Control page panel controls");
    dom.controlPageRoot.appendChild(dock);
  }
  if (!dock) {
    return;
  }
  dock.innerHTML = [
    '<span class="panel-dock-label">Panels</span>',
    ...PANEL_DEFINITIONS.map((definition) => {
      const panel = state.panels[definition.id] || {};
      const pressed = panel.visible !== false ? "true" : "false";
      return '<button type="button" class="panel-dock-button" aria-pressed="' + pressed + '" data-panel-dock="' + definition.id + '">' + escapeHtml(definition.label) + "</button>";
    }),
    '<button type="button" class="panel-dock-button panel-dock-reset" data-panel-reset="1">Reset</button>',
  ].join("");
}

function setPanelVisible(panelId, visible) {
  const panel = state.panels[panelId];
  if (!panel) {
    return;
  }
  panel.visible = Boolean(visible);
  if (panel.visible) {
    panel.z = ++state.panelZIndex;
  }
  applyPanelState(panelId);
  renderPanelDock();
  savePanelLayout();
}

function focusPanel(panelId) {
  const panel = state.panels[panelId];
  if (!panel) {
    return;
  }
  panel.visible = true;
  panel.z = ++state.panelZIndex;
  applyPanelState(panelId);
  panel.element?.focus?.({ preventScroll: true });
  renderPanelDock();
  savePanelLayout();
}

function resetPanelLayout() {
  window.localStorage.removeItem(PANEL_LAYOUT_STORAGE_KEY);
  for (const [index, definition] of PANEL_DEFINITIONS.entries()) {
    const panel = state.panels[definition.id];
    if (!panel) {
      continue;
    }
    const fallback = defaultPanelPosition(index, panel.element);
    panel.visible = true;
    panel.floating = false;
    panel.x = fallback.x;
    panel.y = fallback.y;
    panel.width = fallback.width;
    panel.z = 0;
    applyPanelState(definition.id);
  }
  renderPanelDock();
  savePanelLayout();
}

function beginPanelDrag(event, panelId, { force = false } = {}) {
  if (isCompactPanelMode()) {
    return;
  }
  if (event.button !== undefined && event.button !== 0) {
    return;
  }
  if (!force && event.target.closest("button, a, input, textarea, select, summary, .panel-window-controls")) {
    return;
  }
  const panel = state.panels[panelId];
  if (!panel || panel.visible === false) {
    return;
  }
  const rect = panel.element.getBoundingClientRect();
  panel.floating = true;
  panel.width = rect.width;
  panel.flowHeight = rect.height;
  panel.x = rect.left;
  panel.y = rect.top;
  panel.z = ++state.panelZIndex;
  applyPanelState(panelId);
  const startX = event.clientX;
  const startY = event.clientY;
  const originX = panel.x;
  const originY = panel.y;
  panel.element.classList.add("is-panel-dragging");
  document.body.classList.add("is-panel-dragging-active");
  event.preventDefault();
  const captureTarget = event.currentTarget?.setPointerCapture ? event.currentTarget : event.target;
  captureTarget?.setPointerCapture?.(event.pointerId);

  const move = (moveEvent) => {
    panel.x = originX + moveEvent.clientX - startX;
    panel.y = originY + moveEvent.clientY - startY;
    applyPanelState(panelId);
  };
  const stop = () => {
    panel.element.classList.remove("is-panel-dragging");
    document.body.classList.remove("is-panel-dragging-active");
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
    savePanelLayout();
  };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop, { once: true });
}

function initPanelManager() {
  if (!PANEL_MANAGER_ENABLED) {
    return;
  }
  if (state.panelsReady || !dom.controlPageRoot) {
    return;
  }
  const saved = loadPanelLayout();
  for (const [index, definition] of PANEL_DEFINITIONS.entries()) {
    const element = document.querySelector(definition.selector);
    if (!element) {
      continue;
    }
    const fallback = defaultPanelPosition(index, element);
    const panelState = saved[definition.id] || {};
    const panel = {
      id: definition.id,
      label: definition.label,
      element,
      placeholder: null,
      visible: panelState.visible !== false,
      floating: Boolean(panelState.floating),
      x: Number.isFinite(panelState.x) ? panelState.x : fallback.x,
      y: Number.isFinite(panelState.y) ? panelState.y : fallback.y,
      width: Number.isFinite(panelState.width) && panelState.width > 0 ? panelState.width : fallback.width,
      flowHeight: fallback.height || 0,
      z: Number.isFinite(panelState.z) ? panelState.z : 0,
    };
    const placeholder = document.createElement("div");
    placeholder.className = "ui-panel-placeholder";
    placeholder.setAttribute("aria-hidden", "true");
    element.parentNode?.insertBefore(placeholder, element);
    panel.placeholder = placeholder;
    state.panels[definition.id] = panel;
    element.classList.add("ui-panel");
    element.dataset.panelId = definition.id;
    element.setAttribute("tabindex", "-1");
    const handle = element.querySelector(definition.handleSelector) || element.firstElementChild || element;
    handle.classList.add("ui-panel-handle");
    const controlHost = handle.querySelector(".chat-header-actions") || handle;
    if (!handle.querySelector("[data-panel-close]")) {
      const controls = document.createElement("div");
      controls.className = "panel-window-controls";
      controls.innerHTML = [
        '<button type="button" class="panel-window-button panel-move-button" title="Move panel" data-panel-move="' + definition.id + '">Move</button>',
        '<button type="button" class="panel-window-button" title="Focus panel" data-panel-focus="' + definition.id + '">Focus</button>',
        '<button type="button" class="panel-window-button" title="Close panel" data-panel-close="' + definition.id + '">Close</button>',
      ].join("");
      controlHost.appendChild(controls);
    }
    handle.addEventListener("pointerdown", (event) => beginPanelDrag(event, definition.id));
    applyPanelState(definition.id);
  }
  state.panelsReady = true;
  renderPanelDock();
}

function applyPanelCommands(panelPacket) {
  if (!panelPacket || !Array.isArray(panelPacket.commands)) {
    return;
  }
  for (const command of panelPacket.commands) {
    if (!command || state.panelCommandIds.has(command.id)) {
      continue;
    }
    state.panelCommandIds.add(command.id);
    const action = String(command.action || "").toLowerCase();
    const panelId = command.panel ? String(command.panel) : "";
    if (action === "reset") {
      if (WINBOX_PANEL_MANAGER_ENABLED && state.winboxReady) {
        resetWinBoxLayout();
      } else {
        resetPanelLayout();
      }
      continue;
    }
    if (WINBOX_PANEL_MANAGER_ENABLED && state.winboxReady) {
      if (action === "show") {
        openWinBoxPanel(panelId);
      } else if (action === "hide") {
        closeWinBoxPanel(panelId);
      } else if (action === "toggle") {
        toggleWinBoxPanel(panelId);
      } else if (action === "focus") {
        focusWinBoxPanel(panelId);
      }
      continue;
    }
    if (!state.panels[panelId]) {
      continue;
    }
    if (action === "show") {
      setPanelVisible(panelId, true);
    } else if (action === "hide") {
      setPanelVisible(panelId, false);
    } else if (action === "toggle") {
      setPanelVisible(panelId, state.panels[panelId].visible === false);
    } else if (action === "focus") {
      focusPanel(panelId);
    }
  }
  if (state.panelCommandIds.size > 100) {
    state.panelCommandIds = new Set(Array.from(state.panelCommandIds).slice(-60));
  }
}

function loadWinBoxLayout() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(WINBOX_LAYOUT_STORAGE_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (_error) {
    return {};
  }
}

function saveWinBoxLayout() {
  const next = {};
  for (const definition of PANEL_DEFINITIONS) {
    const panel = state.winboxPanels[definition.id];
    if (!panel) {
      continue;
    }
    next[definition.id] = {
      open: Boolean(panel.open),
      x: Math.round(panel.x || 0),
      y: Math.round(panel.y || 0),
      width: Math.round(panel.width || 0),
      height: Math.round(panel.height || 0),
    };
  }
  window.localStorage.setItem(WINBOX_LAYOUT_STORAGE_KEY, JSON.stringify(next));
}

function defaultWinBoxLayout(panelId, index) {
  const viewportWidth = Math.max(720, window.innerWidth || 1280);
  const viewportHeight = Math.max(520, window.innerHeight || 760);
  const chatWidth = Math.min(520, Math.max(360, Math.round(viewportWidth * 0.36)));
  const chatHeight = Math.min(430, Math.max(320, Math.round(viewportHeight * 0.42)));
  const avatarWidth = Math.min(620, Math.max(430, Math.round(viewportWidth * 0.36)));
  const avatarHeight = Math.min(640, Math.max(430, viewportHeight - 184));
  const avatarX = Math.max(24, Math.min(viewportWidth - avatarWidth - 44, Math.round(viewportWidth * 0.56)));
  const chatX = Math.max(24, Math.min(78, viewportWidth - chatWidth - 24));
  const chatY = Math.max(76, viewportHeight - chatHeight - 92);
  const defaults = {
    runtime: { width: 390, height: 620, x: 32, y: 76 },
    diagnostics: { width: 430, height: 560, x: 88, y: 124 },
    avatar: { width: avatarWidth, height: avatarHeight, x: avatarX, y: 86 },
    chat: { width: chatWidth, height: chatHeight, x: chatX, y: chatY },
    memory: { width: Math.min(860, Math.max(620, Math.round(viewportWidth * 0.52))), height: Math.min(720, viewportHeight - 112), x: 64, y: 72 },
  };
  return defaults[panelId] || { width: 420, height: 520, x: 32 + index * 36, y: 48 + index * 34 };
}

function clampWinBoxLayout(layout, panelId = "") {
  const maxWidthByPanel = {
    runtime: 520,
    diagnostics: 680,
    avatar: 920,
    chat: 580,
    memory: 980,
  };
  const maxHeightByPanel = {
    runtime: 760,
    diagnostics: 720,
    avatar: 780,
    chat: 780,
    memory: 820,
  };
  const widthCap = Math.min(maxWidthByPanel[panelId] || 760, Math.max(320, window.innerWidth - 24));
  const heightCap = Math.min(maxHeightByPanel[panelId] || 760, Math.max(280, window.innerHeight - 24));
  const width = Math.min(Math.max(layout.width || 380, 300), widthCap);
  const height = Math.min(Math.max(layout.height || 420, 240), heightCap);
  return {
    width,
    height,
    x: Math.max(8, Math.min(layout.x || 8, Math.max(8, window.innerWidth - width - 8))),
    y: Math.max(8, Math.min(layout.y || 8, Math.max(8, window.innerHeight - height - 8))),
  };
}

function ensureWinBoxPanelRecord(definition, index) {
  const existing = state.winboxPanels[definition.id];
  if (existing) {
    return existing;
  }
  const element = document.querySelector(definition.selector);
  if (!element) {
    return null;
  }
  const saved = loadWinBoxLayout()[definition.id] || {};
  const fallback = defaultWinBoxLayout(definition.id, index);
  const layout = clampWinBoxLayout({
    x: Number.isFinite(saved.x) ? saved.x : fallback.x,
    y: Number.isFinite(saved.y) ? saved.y : fallback.y,
    width: Number.isFinite(saved.width) && saved.width > 0 ? saved.width : fallback.width,
    height: Number.isFinite(saved.height) && saved.height > 0 ? saved.height : fallback.height,
  }, definition.id);
  const placeholder = document.createElement("div");
  placeholder.className = "winbox-panel-placeholder";
  placeholder.setAttribute("aria-hidden", "true");
  element.parentNode?.insertBefore(placeholder, element);
  return state.winboxPanels[definition.id] = {
    id: definition.id,
    label: definition.label,
    element,
    placeholder,
    winbox: null,
    open: false,
    shouldOpen: typeof saved.open === "boolean" ? saved.open : DEFAULT_OPEN_WINBOX_PANELS.has(definition.id),
    x: layout.x,
    y: layout.y,
    width: layout.width,
    height: layout.height,
  };
}

function setWinBoxPlaceholder(panel, active) {
  if (!panel || !panel.placeholder || !panel.element) {
    return;
  }
  panel.placeholder.style.display = "none";
  panel.placeholder.style.height = "0px";
}

function renderWinBoxDock() {
  let dock = document.querySelector("#winbox-panel-dock");
  if (!dock && dom.controlPageRoot) {
    dock = document.createElement("div");
    dock.id = "winbox-panel-dock";
    dock.className = "panel-dock winbox-panel-dock";
    dock.setAttribute("aria-label", "Floating control panels");
    dom.controlPageRoot.appendChild(dock);
  }
  if (!dock) {
    return;
  }
  dock.innerHTML = [
    '<span class="panel-dock-label">Windows</span>',
    ...PANEL_DEFINITIONS.map((definition) => {
      const panel = state.winboxPanels[definition.id] || {};
      const pressed = panel.open ? "true" : "false";
      return '<button type="button" class="panel-dock-button" aria-pressed="' + pressed + '" data-winbox-panel="' + definition.id + '">' + escapeHtml(definition.label) + "</button>";
    }),
    '<button type="button" class="panel-dock-button panel-dock-reset" data-winbox-reset="1">Reset</button>',
  ].join("");
}

function resizeHandleRotation(handle) {
  if (!handle) {
    return "0deg";
  }
  if (handle.classList.contains("wb-nw")) {
    return "180deg";
  }
  if (handle.classList.contains("wb-ne")) {
    return "270deg";
  }
  if (handle.classList.contains("wb-sw")) {
    return "90deg";
  }
  return "0deg";
}

function setResizeHandleHover(handle, hovered) {
  if (!handle) {
    return;
  }
  handle.classList.toggle("is-resize-handle-hover", hovered);
  const edgeMark = handle.querySelector(".evelyn-resize-edge-mark");
  if (edgeMark) {
    edgeMark.style.opacity = hovered ? "1" : "";
    edgeMark.style.background = hovered ? "var(--winbox-resize-handle-active)" : "";
    edgeMark.style.transform = hovered ? "translateX(-50%) scaleY(1.45)" : "";
  }
  const cornerMark = handle.querySelector(".evelyn-resize-corner-mark");
  if (cornerMark) {
    const rotation = resizeHandleRotation(handle);
    cornerMark.style.opacity = hovered ? "1" : "";
    cornerMark.style.color = hovered ? "var(--winbox-resize-handle-active)" : "";
    cornerMark.style.transform = hovered ? "rotate(" + rotation + ") scale(var(--winbox-resize-corner-hover-scale))" : "";
  }
}

function toggleMemoryPanelLocally() {
  const panel = dom.memoryGraphPanel || document.querySelector("#memory-graph-panel");
  if (!panel) {
    return false;
  }
  if (WINBOX_PANEL_MANAGER_ENABLED && state.winboxReady) {
    toggleWinBoxPanel("memory");
  } else if (state.panels && state.panels.memory) {
    setPanelVisible("memory", state.panels.memory.visible === false);
  } else {
    panel.classList.toggle("winbox-panel-hidden");
    panel.setAttribute("aria-hidden", panel.classList.contains("winbox-panel-hidden") ? "true" : "false");
  }
  if (!panel.classList.contains("winbox-panel-hidden")) {
    panel.setAttribute("tabindex", "-1");
    panel.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => panel.focus({ preventScroll: true }), 120);
    loadMemoryCards({ force: false });
    loadMemoryGraph({ force: false });
  }
  return true;
}

function bindResizeHandleHover(handle) {
  if (!handle || handle.dataset.resizeHoverBound === "1") {
    return;
  }
  handle.dataset.resizeHoverBound = "1";
  handle.addEventListener("pointerenter", () => setResizeHandleHover(handle, true));
  handle.addEventListener("pointerleave", () => setResizeHandleHover(handle, false));
  handle.addEventListener("focusin", () => setResizeHandleHover(handle, true));
  handle.addEventListener("focusout", () => setResizeHandleHover(handle, false));
}

function createResizeCornerMark() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("evelyn-resize-corner-mark");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M17.5 2.5A15 15 0 0 1 2.5 17.5");
  svg.appendChild(path);
  return svg;
}

function ensureWinBoxResizeDecorations(boxElement) {
  if (!boxElement) {
    return;
  }
  const bottomHandle = boxElement.querySelector(".wb-s");
  if (bottomHandle && !bottomHandle.querySelector(".evelyn-resize-edge-mark")) {
    const mark = document.createElement("span");
    mark.className = "evelyn-resize-edge-mark";
    mark.setAttribute("aria-hidden", "true");
    bottomHandle.appendChild(mark);
  }
  bindResizeHandleHover(bottomHandle);

  for (const corner of WINBOX_RESIZE_CORNERS) {
    const handle = boxElement.querySelector(".wb-" + corner);
    if (!handle) {
      continue;
    }
    if (!handle.querySelector(".evelyn-resize-corner-mark")) {
      handle.appendChild(createResizeCornerMark());
    }
    bindResizeHandleHover(handle);
  }
}

function openWinBoxPanel(panelId) {
  const definitionIndex = PANEL_DEFINITIONS.findIndex((item) => item.id === panelId);
  if (definitionIndex < 0 || typeof window.WinBox !== "function") {
    return;
  }
  const definition = PANEL_DEFINITIONS[definitionIndex];
  const panel = ensureWinBoxPanelRecord(definition, definitionIndex);
  if (!panel) {
    return;
  }
  if (panel.winbox) {
    ensureWinBoxResizeDecorations(panel.winbox.g);
    panel.winbox.focus?.();
    return;
  }
  panel.element.classList.remove("winbox-panel-hidden");
  panel.element.classList.add("winbox-panel-content", "winbox-panel-" + panel.id);
  setWinBoxPlaceholder(panel, true);
  const layout = clampWinBoxLayout(panel, panel.id);
  panel.x = layout.x;
  panel.y = layout.y;
  panel.width = layout.width;
  panel.height = layout.height;
  const box = new WinBox({
    title: panel.label,
    class: ["evelyn-winbox", "evelyn-winbox-" + panel.id],
    mount: panel.element,
    x: panel.x,
    y: panel.y,
    width: panel.width,
    height: panel.height,
    minwidth: 300,
    minheight: panel.id === "avatar" ? 460 : 280,
    max: false,
    background: "rgba(26, 24, 22, 0.96)",
    onmove: function (x, y) {
      panel.x = x;
      panel.y = y;
      saveWinBoxLayout();
    },
    onresize: function (width, height) {
      panel.width = width;
      panel.height = height;
      saveWinBoxLayout();
      if (panel.id === "memory" && state.memoryGraphPayload) {
        window.requestAnimationFrame(() => renderMemoryGraph(state.memoryGraphPayload));
      }
    },
    onclose: function () {
      panel.open = false;
      panel.winbox = null;
      setTimeout(() => {
        panel.element.classList.add("winbox-panel-hidden");
        panel.element.classList.remove("winbox-panel-content", "winbox-panel-" + panel.id);
        setWinBoxPlaceholder(panel, false);
        renderWinBoxDock();
        saveWinBoxLayout();
      }, 0);
      return false;
    },
  });
  panel.winbox = box;
  ensureWinBoxResizeDecorations(box.g);
  panel.open = true;
  if (panel.id === "memory") {
    loadMemoryGraph({ force: false });
    setTimeout(() => renderMemoryGraph(state.memoryGraphPayload || { nodes: [], edges: [] }), 80);
  }
  renderWinBoxDock();
  saveWinBoxLayout();
}

function closeWinBoxPanel(panelId) {
  const panel = state.winboxPanels[panelId];
  if (!panel) {
    return;
  }
  if (panel.winbox) {
    panel.winbox.close();
    return;
  }
  panel.open = false;
  panel.element?.classList.add("winbox-panel-hidden");
  setWinBoxPlaceholder(panel, false);
  renderWinBoxDock();
  saveWinBoxLayout();
}

function focusWinBoxPanel(panelId) {
  const panel = state.winboxPanels[panelId];
  if (!panel || !panel.winbox) {
    openWinBoxPanel(panelId);
    return;
  }
  panel.winbox.focus?.();
}

function toggleWinBoxPanel(panelId) {
  const panel = state.winboxPanels[panelId];
  if (panel && panel.open) {
    closeWinBoxPanel(panelId);
    return;
  }
  openWinBoxPanel(panelId);
}

function resetWinBoxLayout() {
  window.localStorage.removeItem(WINBOX_LAYOUT_STORAGE_KEY);
  for (const [index, definition] of PANEL_DEFINITIONS.entries()) {
    const panel = ensureWinBoxPanelRecord(definition, index);
    if (!panel) {
      continue;
    }
    const layout = clampWinBoxLayout(defaultWinBoxLayout(definition.id, index), definition.id);
    panel.x = layout.x;
    panel.y = layout.y;
    panel.width = layout.width;
    panel.height = layout.height;
    panel.shouldOpen = DEFAULT_OPEN_WINBOX_PANELS.has(definition.id);
    if (panel.winbox) {
      panel.winbox.move?.(panel.x, panel.y);
      panel.winbox.resize?.(panel.width, panel.height);
    } else if (panel.shouldOpen) {
      openWinBoxPanel(definition.id);
    } else {
      closeWinBoxPanel(definition.id);
    }
  }
  renderWinBoxDock();
  saveWinBoxLayout();
}

function initWinBoxPanelManager() {
  if (!WINBOX_PANEL_MANAGER_ENABLED || state.winboxReady) {
    return;
  }
  if (typeof window.WinBox !== "function") {
    console.warn("WinBox is not available; keeping the static control page layout.");
    return;
  }
  const records = [];
  for (const [index, definition] of PANEL_DEFINITIONS.entries()) {
    const panel = ensureWinBoxPanelRecord(definition, index);
    if (!panel) {
      continue;
    }
    panel.element.classList.add("winbox-panel-hidden");
    records.push(panel);
  }
  state.winboxReady = true;
  renderWinBoxDock();
  for (const panel of records) {
    if (panel.shouldOpen) {
      openWinBoxPanel(panel.id);
    }
  }
}

function renderState(payload, { preserveScroll = false } = {}) {
  if (!payload) {
    return;
  }
  stopApiWaitingTicker();
  state.apiWaitStartedAt = 0;
  if (!applyBootProgressPayload(payload)) {
    setApiBootWaiting("Runtime state received.");
  }
  state.appState = payload;
  state.commands = payload.commands || [];
  state.allCommands = mergedCommandCatalog(CONTROL_PAGE_COMMAND_CATALOG, state.commands, payload.allCommands || []);

  const voice = payload.voice || {};
  const runtime = payload.runtime || {};
  const services = runtime.services || {};
  const serviceHealth = runtimeHealthFromPayload(payload);
  const runtimeIssue = runtimeHealthHasIssue(serviceHealth);
  const runtimeIssueText = runtimeHealthIssueText(serviceHealth);
  const runtimeIssueDetail = runtimeHealthDetailText(serviceHealth);
  const minecraft = payload.minecraft || {};
  const guild = payload.guild || {};
  applyPanelCommands(runtime.controlPagePanels);
  const ui = resolveUiMode(payload);
  const minecraftActive = ui.mode === "minecraft";
  const minecraftIdleSummary = minecraft.idleSummary || "Minecraft is idle. Connect when ready.";
  const minecraftSnapshotStale = Boolean(minecraft.snapshotStale);
  const minecraftStartupExpected = Boolean(minecraftActive || minecraft.running || ui.submode === "voyager-warmup");
  const hasIssue = Boolean(
    runtimeIssue
      || minecraft.lastError
      || minecraft.snapshotExpired
      || minecraft.snapshotStale
      || (minecraftStartupExpected && (services.voyagerError || services.codexError))
  );

  applyUiMode(ui);

  if (dom.modePill) {
    dom.modePill.textContent = uiModeLabel(ui.mode);
    setStateClasses(dom.modePill, [minecraftActive ? "is-minecraft" : "is-default"], ["is-default", "is-minecraft", "is-warmup", "is-issue", "is-offline"]);
  }
  if (dom.submodePill) {
    dom.submodePill.textContent = uiSubmodeLabel(ui.submode);
    setStateClasses(dom.submodePill, [summaryPillState(ui, hasIssue)], ["is-default", "is-minecraft", "is-warmup", "is-issue", "is-offline"]);
  }
  if (dom.topbarStatusLine) {
    dom.topbarStatusLine.textContent = cleanDisplayText(runtimeIssueText || controlPlane.statusText || payload.statusText, "Checking runtime state.");
  }

  if (dom.avatarStatusCopy) {
    if (voice.speaking) {
      dom.avatarStatusCopy.textContent = cleanDisplayText(voice.ttsTargetName, "voice output") + " is speaking.";
    } else if (ui.submode === "voyager-warmup") {
      dom.avatarStatusCopy.textContent = "Voyager is warming up for Minecraft.";
    } else {
      dom.avatarStatusCopy.textContent = "Voice and runtime status are idle.";
    }
  }

  if (dom.avatarShell) {
    dom.avatarShell.classList.toggle("is-speaking", Boolean(voice.speaking));
  }
  if (dom.ttsTargetName) {
    dom.ttsTargetName.textContent = voice.speaking ? (voice.ttsTargetName || "active") : "idle";
  }
  if (dom.voicePresencePill) {
    dom.voicePresencePill.textContent = ui.submode === "offline"
      ? "offline"
      : (ui.submode === "voyager-warmup" ? "warming" : (voice.speaking ? "speaking" : (voice.listening ? "listening" : "idle")));
    setStateClasses(dom.voicePresencePill, [presencePillState(ui, voice)], ["is-idle", "is-active", "is-warmup", "is-issue", "is-offline"]);
  }
  if (voice.speaking) {
    avatarTalkStart();
  } else {
    avatarTalkStop();
  }

  if (dom.operatorRuntimeTitle) {
    dom.operatorRuntimeTitle.textContent = runtimeIssue
      ? (String((serviceHealth || {}).overallState || "").toLowerCase() === "down" ? "Runtime down" : "Runtime issue")
      : ui.submode === "voyager-warmup"
      ? "Voyager warming"
      : (voice.speaking ? "Evelyn speaking" : (voice.listening ? "Evelyn listening" : "Evelyn ready"));
  }
  if (dom.operatorRuntimeSubcopy) {
    dom.operatorRuntimeSubcopy.textContent = runtimeIssue
      ? cleanDisplayText(runtimeIssueText, "Check runtime state.")
      : ui.submode === "voyager-warmup"
      ? "Minecraft setup is preparing."
      : "Voice, TTS, LLM, and Voyager state are being monitored.";
  }
  if (dom.operatorRuntimeNote) {
    dom.operatorRuntimeNote.textContent = runtimeIssue
      ? cleanDisplayText(runtimeIssueDetail, "Check runtime diagnosis.")
      : ui.submode === "voyager-warmup"
      ? "Waiting for Minecraft mode to attach."
      : "Runtime controls are ready.";
  }
  if (dom.operatorRuntimeDot) {
    dom.operatorRuntimeDot.classList.toggle("is-offline", ui.submode === "offline");
    dom.operatorRuntimeDot.classList.toggle("is-warmup", ui.submode === "voyager-warmup");
    dom.operatorRuntimeDot.classList.toggle("is-issue", runtimeIssue);
  }
  if (dom.operatorStatChannel) dom.operatorStatChannel.textContent = cleanDisplayText(voice.channelName, "none");
  if (dom.operatorStatMode) dom.operatorStatMode.textContent = cleanDisplayText(ui.submode || ui.mode, "default");
  if (dom.operatorStatTts) dom.operatorStatTts.textContent = String(runtime.ttsBacklog || 0);
  if (dom.operatorStatLlm) dom.operatorStatLlm.textContent = String(runtime.inflightLlmRequests || 0);

  const voicePipeline = runtime.voicePipeline || {};
  const outputMode = runtime.outputMode || voicePipeline.outputMode || "unknown";
  const localTtsOutput = runtime.localTtsOutput || voicePipeline.localTtsOutput || {};
  let outputLabel = outputMode === "discord_voice" ? "discord" : outputMode;
  if (outputMode === "local_speaker") {
    const playCount = Number(localTtsOutput.playCount || 0);
    outputLabel = localTtsOutput.active ? "local playing" : (localTtsOutput.lastError ? "local error" : (playCount > 0 ? `local played ${playCount}` : "local ready"));
  }
  if (dom.meterTtsLabel) dom.meterTtsLabel.textContent = `${outputLabel} / ${runtime.ttsBacklog || 0}`;
  if (dom.voicePipelineQueue) dom.voicePipelineQueue.textContent = `${voicePipeline.queueDepth || 0}/${voicePipeline.queueMax || 0}`;
  if (dom.voicePipelineStt) {
    const cooldown = Number(voicePipeline.sttCooldownRemainingSec || 0);
    dom.voicePipelineStt.textContent = voicePipeline.sttBusy ? "busy" : (cooldown > 0 ? `${cooldown.toFixed(1)}s` : "idle");
  }
  if (dom.voicePipelineTts) dom.voicePipelineTts.textContent = `${Math.round(Number(voicePipeline.ttsFirstAudioMsP95 || 0))}ms`;
  if (dom.voicePipelineDrops) {
    const drops = Number(voicePipeline.queueFullDropCount || 0) + Number(voicePipeline.queueStaleDropCount || 0);
    dom.voicePipelineDrops.textContent = String(drops);
  }

  const modelCallMetrics = runtime.modelCallMetrics || {};
  if (dom.modelCallRouterRate) dom.modelCallRouterRate.textContent = formatMetricPercent(modelCallMetrics.routerRouteCallRate);
  if (dom.modelCallRouterLatency) dom.modelCallRouterLatency.textContent = formatMetricMs(modelCallMetrics.routerAvgLatencyMs);
  if (dom.modelCallMainFirst) dom.modelCallMainFirst.textContent = formatMetricMs(modelCallMetrics.mainFirstTokenAvgMs);
  if (dom.modelCallCognitiveRate) dom.modelCallCognitiveRate.textContent = formatMetricPercent(modelCallMetrics.cognitiveBlockingRate);
  if (dom.modelCallSummaryHot) dom.modelCallSummaryHot.textContent = formatMetricPercent(modelCallMetrics.summaryHotPathRate);
  if (dom.modelCallTurnCount) dom.modelCallTurnCount.textContent = String(modelCallMetrics.modelCallCount || 0);

  const questionMetrics = runtime.questionMetrics || {};
  if (dom.questionAddedRate) dom.questionAddedRate.textContent = formatMetricPercent(questionMetrics.questionAddedRate);
  if (dom.questionRemovedCount) dom.questionRemovedCount.textContent = String(questionMetrics.questionRemovedCount || 0);
  if (dom.questionCooldownRate) dom.questionCooldownRate.textContent = formatMetricPercent(questionMetrics.questionCooldownHitRate);
  if (dom.questionAskMode) dom.questionAskMode.textContent = topAskMode(questionMetrics.askModeDistribution);
  if (dom.questionTurnCount) dom.questionTurnCount.textContent = String(questionMetrics.turnCount || 0);
  if (dom.questionFinalCount) dom.questionFinalCount.textContent = String(questionMetrics.finalQuestionCount || 0);

  const localMic = runtime.localMic || {};
  const voiceInputMode = String(localMic.inputMode || "auto").toLowerCase();
  if (dom.voiceInputModeButtons) {
    dom.voiceInputModeButtons.forEach((button) => {
      const mode = String(button.getAttribute("data-voice-input-mode") || "auto").toLowerCase();
      button.setAttribute("aria-pressed", String(mode === voiceInputMode));
    });
  }

  if (dom.minecraftRuntimeTitle) {
    dom.minecraftRuntimeTitle.textContent = minecraftActive ? "Minecraft connected" : (minecraft.running ? "Minecraft starting" : "Minecraft idle");
  }
  if (dom.minecraftRuntimeSubcopy) {
    const liveCopy = minecraft.task || minecraft.goal || minecraft.progress || "Waiting for live Minecraft state.";
    dom.minecraftRuntimeSubcopy.textContent = minecraftActive ? (minecraftSnapshotStale ? (liveCopy + " Fresh state is pending.") : liveCopy) : minecraftIdleSummary;
  }
  if (dom.minecraftRuntimeDot) dom.minecraftRuntimeDot.classList.toggle("is-offline", !minecraftActive);
  if (dom.minecraftIdleNote) dom.minecraftIdleNote.textContent = minecraftIdleSummary;

  if (dom.statCurrentTask) dom.statCurrentTask.textContent = minecraft.task || "idle";
  if (dom.statStage) dom.statStage.textContent = minecraft.stage || "idle";
  if (dom.statUniqueItems) dom.statUniqueItems.textContent = minecraft.uniqueItemCount ?? "-";
  if (dom.statTravelDistance) dom.statTravelDistance.textContent = formatDistance(minecraft.travelDistanceBlocks);
  if (dom.statHealthHunger) dom.statHealthHunger.textContent = formatHealthHunger(minecraft.health, minecraft.hunger);
  if (dom.statSkillLibrary) dom.statSkillLibrary.textContent = minecraft.skillLibrarySize ?? "-";
  renderActivityRows(minecraft.recentActivity || []);

  if (dom.operationsEyebrow) dom.operationsEyebrow.textContent = minecraftActive ? "LIVE TELEMETRY" : "OPERATIONS FEED";
  if (dom.operationsTitle) dom.operationsTitle.textContent = minecraftActive ? "Minecraft operations" : "Runtime operations";
  if (dom.operationsSubcopy) dom.operationsSubcopy.textContent = minecraftActive ? "Live session telemetry is shown here." : "Runtime status and recent activity are shown here.";

  if (dom.systemSummaryPill) {
    dom.systemSummaryPill.textContent = minecraftActive
      ? (minecraftSnapshotStale ? "Live / stale" : "Live")
      : (ui.submode === "voyager-warmup" ? "Warming" : (ui.submode === "offline" ? "Offline" : (runtimeIssue ? "Runtime issue" : (hasIssue ? "Issue" : "Ready"))));
    dom.systemSummaryPill.title = runtimeIssueDetail || services.codexError || services.voyagerError || "";
    setStateClasses(dom.systemSummaryPill, [summaryPillState(ui, hasIssue)], ["is-idle", "is-active", "is-warmup", "is-issue", "is-offline"]);
  }

  setMeter(dom.meterVoyager, dom.meterVoyagerLabel, meterLevel(minecraftActive ? "active" : (minecraft.running ? "warm" : "idle")), minecraftActive ? "connected" : (minecraft.running ? "starting" : "idle"));
  setMeter(dom.meterVoice, dom.meterVoiceLabel, meterLevel(voice.listening ? "active" : "idle"), voice.listening ? "listening" : "idle");
  setMeter(dom.meterTts, dom.meterTtsLabel, voice.speaking ? 100 : 8, voice.speaking ? (voice.ttsTargetName || "active") : "idle");
  setMeter(dom.meterLlm, dom.meterLlmLabel, Math.min(100, (runtime.inflightLlmRequests || 0) * 32), String(runtime.inflightLlmRequests || 0) + " inflight");

  if (dom.guildName) dom.guildName.textContent = guild.name || "Guild not connected";
  if (dom.objectiveGoal) dom.objectiveGoal.textContent = minecraft.goal || "idle";
  if (dom.objectiveProgress) dom.objectiveProgress.textContent = minecraft.progress || "No progress message";
  if (dom.objectiveStage) dom.objectiveStage.textContent = minecraft.stage || "idle";
  if (dom.objectiveTaskStage) dom.objectiveTaskStage.textContent = minecraft.taskStage || "idle";
  if (dom.positionBlock) dom.positionBlock.textContent = minecraft.position || "unknown";
  if (dom.inventorySummary) dom.inventorySummary.textContent = minecraft.inventorySummary || "No inventory summary";
  renderInventoryWidget(minecraft.inventorySummary, minecraft.inventoryTop || [], minecraft.inventorySlots || [], minecraft.inventoryUsedSlots, minecraft.uniqueItemCount);
  if (!minecraftActive) setInventoryWidgetOpen(false);

  if (dom.commandInput) {
    dom.commandInput.placeholder = minecraftActive ? "Message Evelyn" : "Send a message or type /minecraft connect";
  }
  if (dom.composerHintLeft) dom.composerHintLeft.textContent = "Enter to send";
  if (dom.actionsEyebrow) dom.actionsEyebrow.textContent = minecraftActive ? "MISSION ACTIONS" : "CONTROL ACTIONS";
  if (dom.actionsSubcopy) dom.actionsSubcopy.textContent = minecraftActive ? "Mission controls are available." : "Common status and support controls are available.";
  if (dom.primaryActionTitle) dom.primaryActionTitle.textContent = minecraftActive ? "Mission controls" : "Start here";
  if (dom.supportActionTitle) dom.supportActionTitle.textContent = minecraftActive ? "Runtime support" : "More commands";
  if (dom.supportActionCaption) dom.supportActionCaption.textContent = minecraftActive ? "Use support actions while the mission is running." : "Use support actions for status and diagnostics.";

  renderControlBrief(payload);
  renderDefaultViewport(payload, ui);
  renderMinecraftOpsPanel(payload);
  renderQuickCommands();
  renderChat((payload.chat || {}).messages || [], payload.statusText || "Checking connection.", { preserveScroll });
  renderSuggestions();
  loadMemoryCards({ force: false });
  loadMemoryGraph({ force: false });
  schedulePolling();
}

async function refreshState({ preserveScroll = false } = {}) {
  try {
    const payload = await fetchApi("/api/control-page/state");
    renderState(payload, { preserveScroll });
  } catch (_error) {
    state.apiBase = null;
    ensureApiWaitingTicker();
    return;
  }
}

function desiredPollIntervalMs() {
  const minecraft = (state.appState || {}).minecraft || {};
  if (!state.apiBase) {
    return 1800;
  }
  if (minecraft.sessionActive) {
    return minecraft.snapshotStale ? 900 : 1200;
  }
  return 4000;
}

function schedulePolling() {
  const nextIntervalMs = desiredPollIntervalMs();
  if (pollTimer !== null && pollIntervalMs === nextIntervalMs) {
    return;
  }
  if (pollTimer !== null) {
    clearInterval(pollTimer);
  }
  pollIntervalMs = nextIntervalMs;
  pollTimer = window.setInterval(() => {
    if (!state.sending) {
      refreshState({ preserveScroll: true });
    }
  }, pollIntervalMs);
}

async function sendCurrentMessage(rawText) {
  const text = (rawText || "").trim();
  if (!text || state.sending) {
    return;
  }
  const normalized = text.toLowerCase();
  state.sending = true;
  state.inputHistory.unshift(text);
  state.historyIndex = -1;
  if (dom.commandInput) {
    dom.commandInput.value = "";
    autosizeTextarea();
    renderSuggestions();
  }
  if (dom.composerSendButton) {
    dom.composerSendButton.disabled = true;
  }
  try {
    if (normalized === "/" || normalized === "/help") {
      const now = Date.now() / 1000;
      renderChat(
        [
          { role: "user", author: "정훈", text, at: now },
          { role: "assistant", author: "Evelyn", text: formatCommandHelp(state.allCommands), at: now },
        ],
        "Command list is ready."
      );
      return;
    }
    if (normalized === "/memory" || normalized === "/obsidian") {
      const now = Date.now() / 1000;
      const ok = toggleMemoryPanelLocally();
      renderChat(
        [
          { role: "user", author: "정훈", text, at: now },
          {
            role: "assistant",
            author: "Evelyn",
            text: ok ? "Memory panel opened." : "Memory panel is not available in this layout.",
            at: now,
          },
        ],
        ok ? "Memory panel opened." : "Memory panel unavailable."
      );
      return;
    }
    const payload = await fetchApi("/api/control-page/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ text }),
    });
    if (payload.state) {
      renderState(payload.state);
    } else {
      await refreshState();
    }
  } catch (error) {
    renderChat([], "Message send failed: " + error.message);
  } finally {
    state.sending = false;
    if (dom.composerSendButton) {
      dom.composerSendButton.disabled = false;
    }
  }
}

initializeInventoryWidget();

if (dom.commandInput) {
  autosizeTextarea();
  dom.commandInput.addEventListener("input", () => {
    autosizeTextarea();
    state.selectedSuggestionIndex = 0;
    renderSuggestions();
  });

  dom.commandInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (state.suggestionItems.length && dom.commandInput.value.trim().startsWith("/")) {
        sendCurrentMessage(dom.commandInput.value);
        return;
      }
      sendCurrentMessage(dom.commandInput.value);
      return;
    }

    if (event.key === "ArrowUp" && state.suggestionItems.length) {
      event.preventDefault();
      state.selectedSuggestionIndex = (state.selectedSuggestionIndex - 1 + state.suggestionItems.length) % state.suggestionItems.length;
      renderSuggestions();
      return;
    }

    if (event.key === "ArrowDown" && state.suggestionItems.length) {
      event.preventDefault();
      state.selectedSuggestionIndex = (state.selectedSuggestionIndex + 1) % state.suggestionItems.length;
      renderSuggestions();
      return;
    }

    if ((event.key === "Tab" || event.key === "ArrowRight") && state.suggestionItems.length) {
      event.preventDefault();
      applySuggestion(state.selectedSuggestionIndex);
      return;
    }

    if (event.key === "Escape") {
      dom.commandSuggestions?.classList.add("is-hidden");
    }
  });
}

if (dom.commandSuggestions) {
  dom.commandSuggestions.addEventListener("click", (event) => {
    const button = event.target.closest("[data-suggestion-index]");
    if (!button) {
      return;
    }
    const index = Number(button.getAttribute("data-suggestion-index"));
    if (Number.isFinite(index)) {
      applySuggestion(index);
    }
  });
}

if (dom.chatComposer) {
  dom.chatComposer.addEventListener("submit", (event) => {
    event.preventDefault();
    if (dom.commandInput) {
      sendCurrentMessage(dom.commandInput.value);
    }
  });
}

if (dom.chatThread) {
  dom.chatThread.addEventListener("scroll", () => {
    if (isChatScrolledNearBottom()) {
      hideNewChatMessageNotice();
    }
  });
}

if (dom.chatNewMessageButton) {
  dom.chatNewMessageButton.textContent = "새 메시지";
  dom.chatNewMessageButton.addEventListener("click", () => {
    dom.chatThread.scrollTop = dom.chatThread.scrollHeight;
    hideNewChatMessageNotice();
  });
}

if (dom.quickCommandRow) {
  dom.quickCommandRow.addEventListener("click", handleQuickActionClick);
}

if (dom.primaryActionRow) {
  dom.primaryActionRow.addEventListener("click", handleQuickActionClick);
}

if (dom.voiceInputSwitches) {
  dom.voiceInputSwitches.forEach((switchElement) => switchElement.addEventListener("click", (event) => {
    const button = event.target.closest("[data-voice-input-mode]");
    if (!button) {
      return;
    }
    const mode = button.getAttribute("data-voice-input-mode") || "auto";
    sendCurrentMessage("/voice input " + mode);
  }));
}

if (dom.chatShutdownButton) {
  dom.chatShutdownButton.addEventListener("click", () => {
    handleChatCommandTrigger(dom.chatShutdownButton);
  });
}

if (dom.bootSplashShutdownButton) {
  dom.bootSplashShutdownButton.addEventListener("click", requestBootSplashShutdown);
}

if (dom.inventoryToggleButton) {
  dom.inventoryToggleButton.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    setInventoryWidgetOpen(!state.inventoryWidgetOpen);
  });
}

if (dom.inventoryWidgetClose) {
  dom.inventoryWidgetClose.addEventListener("click", (event) => {
    event.preventDefault();
    setInventoryWidgetOpen(false);
  });
}

document.addEventListener("pointerdown", (event) => {
  const moveButton = event.target.closest("[data-panel-move]");
  if (moveButton) {
    beginPanelDrag(event, moveButton.getAttribute("data-panel-move"), { force: true });
    return;
  }
  if (!state.inventoryWidgetOpen || !dom.inventoryCard) {
    return;
  }
  if (event.target instanceof Node && dom.inventoryCard.contains(event.target)) {
    return;
  }
  setInventoryWidgetOpen(false);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.inventoryWidgetOpen) {
    setInventoryWidgetOpen(false);
  }
});

if (dom.refreshStateButton) {
  dom.refreshStateButton.addEventListener("click", () => {
    refreshState();
  });
}

if (dom.memoryGraphRefreshButton) {
  dom.memoryGraphRefreshButton.addEventListener("click", () => {
    loadMemoryCards({ force: true });
    loadMemoryGraph({ force: true });
  });
}

if (dom.memoryCardList) {
  dom.memoryCardList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-memory-action]");
    if (!button) {
      return;
    }
    event.preventDefault();
    handleMemoryCardAction(button);
  });
}

if (dom.memoryGraphFilter) {
  dom.memoryGraphFilter.addEventListener("click", (event) => {
    const sizeButton = event.target.closest("[data-memory-node-size]");
    if (sizeButton) {
      const action = sizeButton.getAttribute("data-memory-node-size") || "";
      const current = Math.max(0.25, Math.min(2, Number(state.memoryGraphNodeScale || 1)));
      if (action === "down") {
        setMemoryGraphNodeScale(Number((current - 0.25).toFixed(2)));
      } else if (action === "up") {
        setMemoryGraphNodeScale(Number((current + 0.25).toFixed(2)));
      } else if (action === "reset") {
        setMemoryGraphNodeScale(1);
      }
      renderMemoryGraphControls(state.memoryGraphPayload || { nodes: [], edges: [] });
      return;
    }
    const button = event.target.closest("[data-memory-filter]");
    if (!button) {
      return;
    }
    state.memoryGraphFilterType = button.getAttribute("data-memory-filter") || "all";
    renderMemoryGraph(state.memoryGraphPayload || { nodes: [], edges: [] });
  });
}

if (dom.memoryGraphCanvas) {
  dom.memoryGraphCanvas.addEventListener("pointermove", (event) => {
    if (state.memoryGraphPointer.holdId) {
      const dx = event.clientX - Number(state.memoryGraphPointer.startClientX || 0);
      const dy = event.clientY - Number(state.memoryGraphPointer.startClientY || 0);
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
        state.memoryGraphPointer.dragId = state.memoryGraphPointer.holdId;
        state.memoryGraphPointer.holdId = "";
        centerMemoryGraphNodeOnPointer(state.memoryGraphPointer.dragId, event);
        state.memoryGraphPointer.hoverId = state.memoryGraphPointer.dragId;
        dom.memoryGraphCanvas.style.cursor = "grabbing";
      }
      return;
    }
    if (state.memoryGraphPointer.dragId) {
      updateMemoryGraphDragPoint(event);
      state.memoryGraphPointer.hoverId = state.memoryGraphPointer.dragId;
      dom.memoryGraphCanvas.style.cursor = "grabbing";
      return;
    }
    const node = nearestMemoryGraphNode(event.clientX, event.clientY);
    state.memoryGraphPointer.hoverId = node ? node.id : "";
    dom.memoryGraphCanvas.style.cursor = node ? "pointer" : "default";
  });
  dom.memoryGraphCanvas.addEventListener("pointerdown", (event) => {
    const node = nearestMemoryGraphNode(event.clientX, event.clientY);
    if (!node) {
      state.memoryGraphSelectedNodeId = "";
      renderMemoryGraphDetail(null);
      return;
    }
    state.memoryGraphSelectedNodeId = node.id;
    state.memoryGraphPointer.dragId = "";
    state.memoryGraphPointer.holdId = node.id;
    state.memoryGraphPointer.down = true;
    state.memoryGraphPointer.startClientX = event.clientX;
    state.memoryGraphPointer.startClientY = event.clientY;
    state.memoryGraphPointer.offsetX = 0;
    state.memoryGraphPointer.offsetY = 0;
    dom.memoryGraphCanvas.setPointerCapture?.(event.pointerId);
    dom.memoryGraphCanvas.style.cursor = "pointer";
    renderMemoryGraphDetail(node);
  });
  dom.memoryGraphCanvas.addEventListener("pointerup", (event) => {
    state.memoryGraphPointer.dragId = "";
    state.memoryGraphPointer.holdId = "";
    state.memoryGraphPointer.down = false;
    state.memoryGraphPointer.offsetX = 0;
    state.memoryGraphPointer.offsetY = 0;
    state.memoryGraphPointer.startClientX = 0;
    state.memoryGraphPointer.startClientY = 0;
    dom.memoryGraphCanvas.releasePointerCapture?.(event.pointerId);
  });
  dom.memoryGraphCanvas.addEventListener("pointercancel", (event) => {
    state.memoryGraphPointer.dragId = "";
    state.memoryGraphPointer.holdId = "";
    state.memoryGraphPointer.down = false;
    state.memoryGraphPointer.offsetX = 0;
    state.memoryGraphPointer.offsetY = 0;
    state.memoryGraphPointer.startClientX = 0;
    state.memoryGraphPointer.startClientY = 0;
    dom.memoryGraphCanvas.releasePointerCapture?.(event.pointerId);
  });
  dom.memoryGraphCanvas.addEventListener("pointerleave", () => {
    state.memoryGraphPointer.hoverId = "";
    if (!state.memoryGraphPointer.down) {
      state.memoryGraphPointer.dragId = "";
      state.memoryGraphPointer.holdId = "";
    }
  });
}

document.addEventListener("click", (event) => {
  const winboxButton = event.target.closest("[data-winbox-panel]");
  if (winboxButton) {
    event.preventDefault();
    toggleWinBoxPanel(winboxButton.getAttribute("data-winbox-panel"));
    return;
  }
  if (event.target.closest("[data-winbox-reset]")) {
    event.preventDefault();
    resetWinBoxLayout();
    return;
  }
  const closeButton = event.target.closest("[data-panel-close]");
  if (closeButton) {
    event.preventDefault();
    setPanelVisible(closeButton.getAttribute("data-panel-close"), false);
    return;
  }
  const focusButton = event.target.closest("[data-panel-focus]");
  if (focusButton) {
    event.preventDefault();
    focusPanel(focusButton.getAttribute("data-panel-focus"));
    return;
  }
  const dockButton = event.target.closest("[data-panel-dock]");
  if (dockButton) {
    event.preventDefault();
    const panelId = dockButton.getAttribute("data-panel-dock");
    setPanelVisible(panelId, !(state.panels[panelId] && state.panels[panelId].visible !== false));
    return;
  }
  if (event.target.closest("[data-panel-reset]")) {
    event.preventDefault();
    resetPanelLayout();
  }
});

window.addEventListener("resize", () => {
  for (const definition of PANEL_DEFINITIONS) {
    applyPanelState(definition.id);
  }
  if (state.memoryGraphPayload) {
    renderMemoryGraph(state.memoryGraphPayload);
  }
});

initPanelManager();
initWinBoxPanelManager();
initAvatarInteractions();
initWallpaperPicker();
ensureApiWaitingTicker();
refreshState();
schedulePolling();

const RUNTIME_REPAIR_SERVICE_PRIORITY = ["main_llm", "router_llm", "sub_llm", "tts", "bot_api", "control_page"];
const RUNTIME_REPAIR_SERVICE_LABEL = {
  main_llm: "Main LLM",
  router_llm: "Router LLM",
  sub_llm: "Sub LLM",
  tts: "TTS",
  bot_api: "Bot API",
  control_page: "Control-Page",
};
const RUNTIME_REPAIR_SERVICE_ACTION_LABEL = {
  main_llm: "Preview Main LLM repair",
  router_llm: "Preview Router LLM repair",
  sub_llm: "Preview Sub LLM repair",
  tts: "Preview TTS repair",
  bot_api: "Preview Bot API repair",
  control_page: "Preview Control-Page repair",
};

function runtimeRepairServiceLabel(serviceId) {
  return RUNTIME_REPAIR_SERVICE_LABEL[String(serviceId || "")] || String(serviceId || "Unknown service");
}

function runtimeRepairServiceActionLabel(serviceId, fallback) {
  return RUNTIME_REPAIR_SERVICE_ACTION_LABEL[String(serviceId || "")] || fallback || "Preview runtime repair";
}

function runtimeRepairActionIdFromServiceRow(row) {
  const serviceId = String(row && row.id || "").trim();
  if (!serviceId) {
    return "";
  }
  const actions = Array.isArray(row.suggestedActions) ? row.suggestedActions : [];
  const action = actions.find((item) => item && item.id);
  return action && action.id ? String(action.id) : `start_${serviceId}`;
}

function runtimeRepairBlockingServices(health) {
  const services = Array.isArray(health && health.services) ? health.services : [];
  const serviceById = {};
  for (const service of services) {
    const serviceId = String(service && service.id || "").trim();
    if (!serviceId) {
      continue;
    }
    serviceById[serviceId] = service;
  }
  const isBlockingState = (value) => {
    const state = String(value || "").toLowerCase();
    return state === "down" || state === "partial" || state === "unknown";
  };
  const candidates = [];
  const seen = new Set();

  const addService = (serviceId) => {
    if (!serviceId || seen.has(serviceId)) {
      return;
    }
    const service = serviceById[serviceId];
    seen.add(serviceId);
    if (!service || !service.required || !isBlockingState(service.state)) {
      return;
    }
    const actionId = runtimeRepairActionIdFromServiceRow(service);
    if (!actionId) {
      return;
    }
    candidates.push({
      serviceId,
      label: runtimeRepairServiceLabel(service.label || serviceId),
      state: String(service.state || "").toLowerCase(),
      actionId,
      required: true,
    });
  };

  for (const serviceId of RUNTIME_REPAIR_SERVICE_PRIORITY) {
    addService(serviceId);
  }
  for (const service of services) {
    addService(String(service && service.id || "").trim());
  }
  return candidates;
}

function runtimeRepairSummaryForBlockingServices(blockingServices) {
  if (!Array.isArray(blockingServices) || !blockingServices.length) {
    return "";
  }
  const first = blockingServices[0];
  const firstName = String(first && first.label || "").trim() || "first repair target";
  const followUp = blockingServices.slice(1).map((service) => String(service && service.label || "").trim()).filter(Boolean);
  if (!followUp.length) {
    return `Health check recommends repairing ${firstName} first.`;
  }
  return `Health check recommends repairing ${firstName} first. Recheck ${followUp[0]} after ${firstName}.`;
}

function runtimeRepairActionFromPayload(payload) {
  const health = runtimeHealthFromPayload(payload);
  if (!runtimeHealthHasIssue(health)) {
    return null;
  }
  const blockingServices = runtimeRepairBlockingServices(health);
  const preferred = blockingServices[0];
  const diagnostic = primaryRuntimeDiagnostic(health);
  const diagnosticActions = Array.isArray(diagnostic && diagnostic.suggestedActions) ? diagnostic.suggestedActions : [];
  const codeText = runtimeHealthCodeText(health);
  const action = preferred
    ? { id: preferred.actionId, serviceId: preferred.serviceId }
    : (diagnosticActions[0] || null);
  if (!action || !action.id) {
    return null;
  }
  const actionId = String(action.id);
  const serviceId = String(action.serviceId || (actionId.startsWith("start_") ? actionId.slice("start_".length) : ""));
  const summary = runtimeRepairSummaryForBlockingServices(blockingServices)
    || codeText?.repairActionSummary
    || "Preview the runtime repair plan before starting anything.";
  return {
    repairPreview: true,
    actionId,
    serviceId,
    command: "repair-preview",
    template: "repair-preview",
    label: runtimeRepairServiceActionLabel(serviceId, codeText?.repairActionLabel),
    summary,
    blockingServices,
    recommendedOrder: blockingServices,
    runtimeHealthSummary: runtimeHealthIssueText(health),
  };
}
