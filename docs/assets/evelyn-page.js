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
  controlPageRoot: document.querySelector("#control-page-root"),
  modelViewport: document.querySelector(".model-viewport"),
  chatThread: document.querySelector("#chat-thread"),
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
  memoryGraphPointer: { x: 0, y: 0, down: false, dragId: "", hoverId: "" },
  memoryCardsPayload: null,
  memoryCardsLoading: false,
  memoryCardsLastLoadedAt: 0,
};

let pollTimer = null;
let pollIntervalMs = 0;
let apiWaitingTicker = null;
const PANEL_LAYOUT_STORAGE_KEY = "evelyn.controlPage.panels.v2";
const PANEL_MANAGER_ENABLED = false;
const WINBOX_PANEL_MANAGER_ENABLED = true;
const WINBOX_LAYOUT_STORAGE_KEY = "evelyn.controlPage.winbox.v2";
const DEFAULT_OPEN_WINBOX_PANELS = new Set(["avatar", "chat"]);
const PANEL_DEFINITIONS = [
  { id: "runtime", label: "Runtime", selector: ".context-card", handleSelector: ".panel-title-row" },
  { id: "diagnostics", label: "Diagnostics", selector: "#minecraft-telemetry-panel", handleSelector: ".panel-title-row" },
  { id: "avatar", label: "Avatar", selector: ".model-viewport", handleSelector: ".viewport-topbar" },
  { id: "chat", label: "Chat", selector: ".chat-panel", handleSelector: ".chat-header" },
  { id: "memory", label: "Memory", selector: "#memory-graph-panel", handleSelector: ".memory-graph-header" },
];
const CONTROL_PAGE_COMMAND_CATALOG = [
  { command: "/help", template: "/help", summary: "Show the control page command list" },
  { command: "/status", template: "/status", summary: "Show current Evelyn, voice, and TTS status" },
  { command: "/voice input auto", template: "/voice input auto", summary: "Use local mic with Discord fallback" },
  { command: "/voice input local", template: "/voice input local", summary: "Use local microphone input" },
  { command: "/voice input discord", template: "/voice input discord", summary: "Use Discord voice input" },
  { command: "/inventory", template: "/inventory", summary: "Show the current Minecraft inventory summary" },
  { command: "/voyager stats", template: "/voyager stats", summary: "Show Voyager progress and evaluator status" },
  { command: "/minecraft status", template: "/minecraft status", summary: "Show Minecraft connection and current task status" },
  { command: "/minecraft connect", template: "/minecraft connect", summary: "Start Voyager Minecraft mode" },
  { command: "/minecraft disconnect", template: "/minecraft disconnect", summary: "Stop Voyager Minecraft mode" },
  { command: "/minecraft goal <goal>", template: "/minecraft goal ", summary: "Change the current Minecraft goal" },
  { command: "/autonomy status", template: "/autonomy status", summary: "Show Evelyn autonomy engine status" },
  { command: "/shutdown", template: "/shutdown", summary: "Shut down the full Evelyn stack" },
  { command: "/windows", template: "/windows", summary: "List background console windows and their state" },
  { command: "/show <window>", template: "/show ", summary: "Bring a background console window to front" },
  { command: "/ui <action> <panel>", template: "/ui ", summary: "Show, hide, toggle, focus, or reset control page panels" },
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
  "Evelyn 봇이 응답하기를 기다리고 있습니다.",
  "로컬 control page API 연결을 다시 확인하는 중입니다.",
  "Voyager, voice, runtime 상태를 순서대로 깨우는 중입니다.",
  "연결되면 최근 상태와 명령 버튼을 바로 불러옵니다.",
];

const apiWaitingHints = [
  "보통 start.bat 실행 직후 수 초 안에 자동 연결됩니다.",
  "창이 여러 개 떠도 괜찮습니다. API가 먼저 응답하면 페이지가 자동 전환됩니다.",
  "직접 열 경우 기본 주소는 http://127.0.0.1:8799/ 입니다.",
  "초기 부팅 중에는 모델, 음성, Minecraft 상태가 순차적으로 붙습니다.",
];

function clampPercent(value) {
  return Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
}

function apiBootProgressForElapsed(elapsedMs) {
  const elapsed = Math.max(0, Number(elapsedMs) || 0);
  const eased = 1 - Math.exp(-elapsed / 8500);
  return Math.min(92, 10 + Math.round(eased * 82));
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
  const nextPhase = phase || "API 연결 확인 중";
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

function hideApiBootProgressSoon() {
  window.setTimeout(() => {
    if (state.apiBase) {
      setApiBootProgress(100, "API 연결 완료", { hide: true });
    }
  }, 650);
}

function applyBootProgressPayload(payload) {
  const progress = (payload && (payload.bootProgress || ((payload.runtime || {}).bootProgress))) || null;
  if (!progress || typeof progress !== "object") {
    return false;
  }
  const percent = clampPercent(progress.percent);
  const phase = progress.phase || "부팅 상태 확인 중";
  const ready = percent >= 100 && progress.ready !== false && payload.ok !== false;
  setApiBootProgress(percent, phase, { hide: ready });
  if (ready) {
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
  const bootPercent = apiBootProgressForElapsed(elapsedMs);
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
      text: "127.0.0.1:8799 응답 대기 중 · " + bootPercent + "% · elapsed " + elapsedLabel,
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
  const bootPercent = apiBootProgressForElapsed(elapsedMs);
  setApiBootProgress(bootPercent, apiWaitingSequence[phaseIndex]);
  renderChat(
    buildApiWaitingMessages(),
    "로컬 control page API 응답을 기다리는 중입니다.",
    { preserveScroll }
  );
  const ui = { mode: "default", submode: "booting", reason: "api_waiting" };
  applyUiMode(ui);
  setStateClasses(dom.modePill, ["is-default"], ["is-default", "is-minecraft", "is-warmup", "is-issue", "is-offline"]);
  setStateClasses(dom.submodePill, ["is-warmup"], ["is-default", "is-minecraft", "is-warmup", "is-issue", "is-offline"]);
  setStateClasses(dom.systemSummaryPill, ["is-warmup"], ["is-idle", "is-active", "is-warmup", "is-issue", "is-offline"]);
  setStateClasses(dom.voicePresencePill, ["is-warmup"], ["is-idle", "is-active", "is-warmup", "is-issue", "is-offline"]);
  if (dom.modePill) {
    dom.modePill.textContent = uiModeLabel(ui.mode);
  }
  if (dom.submodePill) {
    dom.submodePill.textContent = uiSubmodeLabel(ui.submode);
  }
  if (dom.topbarStatusLine) {
    dom.topbarStatusLine.textContent = "로컬 control page API 응답을 기다리는 중입니다. " + bootPercent + "%";
  }
  if (dom.systemSummaryPill) {
    dom.systemSummaryPill.textContent = "booting";
  }
  if (dom.voicePresencePill) {
    dom.voicePresencePill.textContent = "준비 중";
  }
  if (dom.operationsEyebrow) {
    dom.operationsEyebrow.textContent = "OPERATIONS FEED";
  }
  if (dom.operationsTitle) {
    dom.operationsTitle.textContent = "부팅 흐름";
  }
  if (dom.operationsSubcopy) {
    dom.operationsSubcopy.textContent = "Evelyn 운영 상태와 로컬 API 연결을 먼저 올리는 중입니다.";
  }
  if (dom.actionsEyebrow) {
    dom.actionsEyebrow.textContent = "CONTROL ACTIONS";
  }
  if (dom.actionsSubcopy) {
    dom.actionsSubcopy.textContent = "API 응답이 붙으면 운영 액션과 대화 입력이 현재 상태에 맞게 채워집니다.";
  }
  if (dom.primaryActionTitle) {
    dom.primaryActionTitle.textContent = "부팅 중";
  }
  if (dom.supportActionTitle) {
    dom.supportActionTitle.textContent = "대기 중";
  }
  if (dom.supportActionCaption) {
    dom.supportActionCaption.textContent = "API가 붙기 전까지는 실제 제어 버튼 대신 상태 확인 힌트만 보여줍니다.";
  }
  if (dom.avatarStatusCopy) {
    dom.avatarStatusCopy.textContent = "Evelyn control page가 로컬 API 응답을 기다리는 동안 상태를 계속 다시 확인합니다.";
  }
  if (dom.composerHintLeft) {
    dom.composerHintLeft.textContent = "API 연결 대기 중";
  }
  if (dom.controlBriefTitle) {
    dom.controlBriefTitle.textContent = "연결 상태 확인 중";
  }
  if (dom.controlBriefBody) {
    dom.controlBriefBody.textContent = "Evelyn과 control page API가 아직 응답하지 않았습니다.";
  }
  if (dom.controlNextTitle) {
    dom.controlNextTitle.textContent = "지금 확인할 것";
  }
  if (dom.controlNextBody) {
    dom.controlNextBody.textContent = "start.bat과 control page API가 모두 올라오면 현재 상태에 맞는 추천 액션을 바로 안내합니다.";
  }
  if (dom.controlIssueCard) {
    dom.controlIssueCard.classList.remove("control-hidden");
  }
  if (dom.controlIssueTitle) {
    dom.controlIssueTitle.textContent = "대기 상태";
  }
  if (dom.controlIssueBody) {
    dom.controlIssueBody.textContent = "127.0.0.1:8799 응답을 기다리는 중입니다.";
  }
  if (dom.quickCommandCaption) {
    dom.quickCommandCaption.textContent = "API가 뜨면 즉시 실행 가능한 버튼으로 바뀝니다.";
  }
  if (dom.operatorRuntimeTitle) {
    dom.operatorRuntimeTitle.textContent = "연결 상태 확인 중";
  }
  if (dom.operatorRuntimeSubcopy) {
    dom.operatorRuntimeSubcopy.textContent = "control page API 응답을 기다리는 중입니다.";
  }
  if (dom.operatorRuntimeNote) {
    dom.operatorRuntimeNote.textContent = "Evelyn, voice, Voyager, runtime 순서로 상태를 다시 확인합니다.";
  }
  if (dom.operatorStatChannel) {
    dom.operatorStatChannel.textContent = "없음";
  }
  if (dom.operatorStatMode) {
    dom.operatorStatMode.textContent = "booting";
  }
  if (dom.operatorStatTts) {
    dom.operatorStatTts.textContent = "0";
  }
  if (dom.operatorStatLlm) {
    dom.operatorStatLlm.textContent = "0";
  }
  if (dom.defaultFocusTitle) {
    dom.defaultFocusTitle.textContent = "연결 상태 확인 중";
  }
  if (dom.defaultFocusBody) {
    dom.defaultFocusBody.textContent = "Evelyn과 control page API 응답이 붙으면 기본 운영 화면이 자동으로 완성됩니다.";
  }
  if (dom.defaultFocusRecentTitle) {
    dom.defaultFocusRecentTitle.textContent = "아직 없음";
  }
  if (dom.defaultFocusRecentBody) {
    dom.defaultFocusRecentBody.textContent = "최근 assistant 응답이 생기면 여기서 바로 요약합니다.";
  }
  if (dom.defaultFocusContextTitle) {
    dom.defaultFocusContextTitle.textContent = "부팅 중";
  }
  if (dom.defaultFocusContextBody) {
    dom.defaultFocusContextBody.textContent = "mode booting · API 응답 대기 · 추천 액션 준비 중";
  }
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
  if (dom.primaryActionRow) {
    dom.primaryActionRow.innerHTML = waitingButtons;
  }
  if (dom.quickCommandRow) {
    dom.quickCommandRow.innerHTML = waitingButtons;
  }
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
    dom.inventoryWidgetList.innerHTML = '<p class="inventory-widget-empty">표시할 인벤토리 항목이 없습니다.</p>';
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
    dom.inventorySummary.textContent = summary || "인벤토리 정보 없음";
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
      ? (hasAnyItem ? (String(normalizedUsed) + "/36 사용 중") : "0/36 사용 중 · 비어 있음")
      : "인벤토리 정보 없음";
  }
  if ((!Array.isArray(entries) || !entries.length) && !hasSlotLayout) {
    dom.inventoryWidgetList.innerHTML = '<p class="inventory-widget-empty">표시할 인벤토리 항목이 없습니다.</p>';
    return;
  }
  dom.inventoryWidgetList.innerHTML = [
    '<div class="inventory-widget-meta">',
    '<span class="inventory-widget-stat">' + escapeHtml(String(normalizedUsed)) + '/36 사용 중</span>',
    '<span class="inventory-widget-stat">' + escapeHtml(String(normalizedUnique)) + '종류</span>',
    '</div>',
    '<div class="inventory-board">',
    '<div class="inventory-side-column">',
    '<p class="inventory-section-title">방어구</p>',
    '<div class="inventory-grid inventory-grid-armor">' + sections.armor.map(renderInventorySlot).join("") + '</div>',
    '</div>',
    '<div class="inventory-main-column">',
    '<p class="inventory-section-title">인벤토리</p>',
    '<div class="inventory-grid inventory-grid-main">' + sections.main.map(renderInventorySlot).join("") + '</div>',
    '<p class="inventory-section-title inventory-section-title-hotbar">핫바</p>',
    '<div class="inventory-grid inventory-grid-hotbar">' + sections.hotbar.map(renderInventorySlot).join("") + '</div>',
    '</div>',
    '<div class="inventory-side-column inventory-side-column-offhand">',
    '<p class="inventory-section-title">보조 손</p>',
    '<div class="inventory-grid inventory-grid-offhand">' + sections.offhand.map(renderInventorySlot).join("") + '</div>',
    '</div>',
    '</div>',
    renderInventoryLedger(entries),
  ].join("");
}

function cleanDisplayText(value, fallback = "없음") {
  const text = value == null ? "" : String(value).trim();
  return text || fallback;
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
      return "Evelyn 상태";
    case "/voice input auto":
      return "Voice auto";
    case "/voice input local":
      return "Local mic";
    case "/voice input discord":
      return "Discord voice";
    case "/inventory":
      return "인벤토리";
    case "/voyager stats":
      return "Voyager 지표";
    case "/minecraft status":
      return "Minecraft 상태";
    case "/minecraft connect":
      return "Minecraft 시작";
    case "/minecraft disconnect":
      return "Minecraft 중지";
    case "/autonomy status":
      return "자율 상태";
    case "/help":
      return "도움말";
    case "/shutdown":
      return "종료";
    case "/windows":
      return "윈도우 목록";
    default:
      return item && (item.command || item.template) ? String(item.command || item.template) : "명령";
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
    return !command.startsWith("/show ") && command !== "/shutdown" && command !== "/windows" && command !== "/minecraft goal <goal>";
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
    '<button type="button" class="quick-command' + (((item.command || "") === "/shutdown") ? ' is-danger' : '') + '" data-chat-command="' + escapeHtml(item.template || item.command) + '" data-chat-send="' + (((item.template || item.command) === item.command) ? "1" : "0") + '" data-confirm="' + (((item.command || "") === "/shutdown") ? "Evelyn 봇을 종료할까요?" : "") + '" title="' + escapeHtml(item.summary || "") + '">' +
      escapeHtml(commandDisplayName(item)) +
    "</button>"
  )).join("");
}

function describeControlState(payload) {
  const guild = (payload && payload.guild) || {};
  const voice = (payload && payload.voice) || {};
  const runtime = (payload && payload.runtime) || {};
  const services = runtime.services || {};
  const minecraft = (payload && payload.minecraft) || {};
  const issues = [];
  if (minecraft.lastError) {
    issues.push("Minecraft 오류: " + cleanDisplayText(minecraft.lastError, "-"));
  }
  if (minecraft.snapshotExpired) {
    issues.push("Minecraft 스냅샷이 오래되어 실제 상태와 다를 수 있습니다.");
  } else if (minecraft.snapshotStale) {
    issues.push("Minecraft 상태가 잠시 늦게 갱신되고 있습니다.");
  }
  if (services.voyagerError) {
    issues.push("Voyager: " + cleanDisplayText(services.voyagerError, "-"));
  }
  if (services.codexError) {
    issues.push("Codex: " + cleanDisplayText(services.codexError, "-"));
  }

  if (!guild.name) {
    return {
      title: "연결된 길드 없음",
      body: "Evelyn이 아직 활성 길드에 연결되지 않았습니다.",
      nextTitle: "추천 액션",
      nextBody: "봇 실행 상태를 먼저 확인한 뒤 /status 로 현재 런타임을 확인하세요.",
      issueTitle: issues.length ? "주의" : "",
      issueBody: issues[0] || "",
      showIssue: Boolean(issues.length),
      quickCaption: "길드가 붙으면 여기서 바로 실행 버튼이 정리됩니다.",
    };
  }

  if (minecraft.sessionActive) {
    const focus = cleanDisplayText(minecraft.task, "") || cleanDisplayText(minecraft.goal, "없음");
    return {
      title: "Minecraft 세션 진행 중",
      body: focus === "없음"
        ? "Minecraft 세션은 연결되어 있고, 최근 스냅샷을 계속 반영하는 중입니다."
        : "현재 집중 대상은 " + focus + " 입니다.",
      nextTitle: "추천 액션",
      nextBody: "인벤토리와 Minecraft 상태를 먼저 확인하고, 필요하면 목표를 조정하거나 세션을 정리하세요.",
      issueTitle: issues.length ? "주의" : "",
      issueBody: issues[0] || "",
      showIssue: Boolean(issues.length),
      quickCaption: "지금 세션에서 바로 확인하거나 멈출 수 있는 액션입니다.",
    };
  }

  if (minecraft.running) {
    return {
      title: "Voyager 준비 중",
      body: "Voyager는 올라와 있지만 아직 실제 Minecraft 플레이 스냅샷은 완전히 붙지 않았습니다.",
      nextTitle: "추천 액션",
      nextBody: "Minecraft 상태를 다시 확인하고, 연결이 늦으면 최근 흐름과 오류를 먼저 보세요.",
      issueTitle: issues.length ? "주의" : "",
      issueBody: issues[0] || "",
      showIssue: Boolean(issues.length),
      quickCaption: "세션 연결 직전이나 워밍업 단계에서 자주 쓰는 액션입니다.",
    };
  }

  if (voice.speaking) {
    return {
      title: "Evelyn 응답 중",
      body: cleanDisplayText(voice.ttsTargetName, "대상 없음") + "에게 TTS 출력 중입니다.",
      nextTitle: "추천 액션",
      nextBody: "지금은 응답이 끝날 때까지 상태 확인 위주로 보는 편이 안전합니다.",
      issueTitle: issues.length ? "주의" : "",
      issueBody: issues[0] || "",
      showIssue: Boolean(issues.length),
      quickCaption: "응답 중에도 안전하게 확인 가능한 액션입니다.",
    };
  }

  return {
    title: "Evelyn 대기 중",
    body: voice.listening
      ? "음성 입력을 들을 준비는 되어 있고, Minecraft 세션은 아직 비활성입니다."
      : "현재는 유휴 상태입니다. 필요한 작업을 여기서 바로 시작할 수 있습니다.",
    nextTitle: "추천 액션",
    nextBody: "Minecraft를 시작하거나, 먼저 Evelyn 상태와 자율 상태를 확인하세요.",
    issueTitle: issues.length ? "주의" : "",
    issueBody: issues[0] || "",
    showIssue: Boolean(issues.length),
    quickCaption: "지금 상태에서 가장 자주 쓰는 기본 액션입니다.",
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
    dom.controlIssueTitle.textContent = brief.issueTitle || "주의";
  }
  if (dom.controlIssueBody) {
    dom.controlIssueBody.textContent = brief.issueBody || "특이사항이 없습니다.";
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
    dom.defaultFocusRecentTitle.textContent = latestAssistant
      ? cleanDisplayText(latestAssistant.author, "Evelyn")
      : "아직 없음";
  }
  if (dom.defaultFocusRecentBody) {
    dom.defaultFocusRecentBody.textContent = latestAssistant
      ? cleanDisplayText(latestAssistant.text, "최근 assistant 응답이 없습니다.")
      : "최근 assistant 응답이 생기면 여기서 바로 요약합니다.";
  }
  if (dom.defaultFocusContextTitle) {
    dom.defaultFocusContextTitle.textContent = guild.name
      ? cleanDisplayText(guild.name, "Guild 미연결")
      : "Guild 미연결";
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
    dom.defaultFocusContextBody.textContent = contextParts.filter(Boolean).join(" · ");
  }
}

function renderMinecraftOpsPanel(payload) {
  const minecraft = (payload && payload.minecraft) || {};
  if (dom.minecraftOpsTitle) {
    dom.minecraftOpsTitle.textContent = minecraft.task || minecraft.goal || "Minecraft 세션 진행 중";
  }
  if (dom.minecraftOpsBody) {
    dom.minecraftOpsBody.textContent = minecraft.progress || minecraft.idleSummary || "진행 메시지를 기다리는 중입니다.";
  }
  if (dom.minecraftOpsInventoryTitle) {
    dom.minecraftOpsInventoryTitle.textContent = minecraft.inventorySummary || "인벤토리 정보 없음";
  }
  if (dom.minecraftOpsInventoryBody) {
    const inventoryTop = Array.isArray(minecraft.inventoryTop) ? minecraft.inventoryTop.slice(0, 3) : [];
    dom.minecraftOpsInventoryBody.textContent = inventoryTop.length
      ? inventoryTop.map((item) => cleanDisplayText(item.name, "item") + " x" + String(item.count || 0)).join(", ")
      : "상위 인벤토리 항목이 아직 없습니다.";
  }
  if (dom.minecraftOpsSurvivalTitle) {
    dom.minecraftOpsSurvivalTitle.textContent = formatHealthHunger(minecraft.health, minecraft.hunger);
  }
  if (dom.minecraftOpsSurvivalBody) {
    const position = cleanDisplayText(minecraft.position, "미확인");
    const hostiles = minecraft.hostiles == null ? "미확인" : String(minecraft.hostiles);
    dom.minecraftOpsSurvivalBody.textContent = "위치 " + position + " · hostiles " + hostiles;
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
  setApiBootProgress(Math.max(state.apiBootProgress, 6), "API 후보 주소 확인 중");
  for (const [index, candidate] of candidates.entries()) {
    try {
      setApiBootProgress(Math.max(state.apiBootProgress, 18 + (index * 18)), candidate + " 확인 중");
      const response = await fetch(candidate + "/api/control-page/state", { cache: "no-store" });
      if (!response.ok) {
        continue;
      }
      setApiBootProgress(Math.max(state.apiBootProgress, 84), "API 응답 수신 중");
      const payload = await response.json();
      state.apiBase = candidate;
      applyBootProgressPayload(payload);
      return payload;
    } catch (_error) {
      // try next
    }
  }
  state.apiBase = null;
  setApiBootProgress(Math.max(state.apiBootProgress, 24), "API 응답 대기 중");
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

function mergedCommandCatalog(...groups) {
  const byCommand = new Map();
  for (const group of groups) {
    if (!Array.isArray(group)) {
      continue;
    }
    for (const item of group) {
      if (!item || !item.command) {
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
      "<strong>아직 활동 기록이 없습니다.</strong>",
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
        { command: "/inventory", template: "/inventory", summary: "현재 Minecraft 인벤토리 요약 보기" },
        { command: "/minecraft status", template: "/minecraft status", summary: "Minecraft 연결과 현재 task 상태 보기" },
        { command: "/minecraft disconnect", template: "/minecraft disconnect", summary: "Voyager Minecraft 모드 중지" },
        { command: "/shutdown", template: "/shutdown", summary: "Shut down the full Evelyn stack" },
        { command: "/help", template: "/help", summary: "페이지에서 지원하는 명령 목록 보기" },
      ]
    : [
        { command: "/minecraft connect", template: "/minecraft connect", summary: "Voyager Minecraft 모드 시작" },
        { command: "/status", template: "/status", summary: "현재 Evelyn, 음성, TTS 상태 보기" },
        { command: "/autonomy status", template: "/autonomy status", summary: "Evelyn 자율 행동 엔진 상태 보기" },
        { command: "/shutdown", template: "/shutdown", summary: "Shut down the full Evelyn stack" },
        { command: "/help", template: "/help", summary: "페이지에서 지원하는 명령 목록 보기" },
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
  if (dom.primaryActionRow) {
    dom.primaryActionRow.innerHTML = commandButtonMarkup(primaryActions);
  }
  if (dom.quickCommandRow) {
    dom.quickCommandRow.innerHTML = commandButtonMarkup(supportActions);
  }
}

function handleChatCommandTrigger(button) {
  if (!button) {
    return;
  }
  const command = button.getAttribute("data-chat-command") || "";
  const confirmMessage = button.getAttribute("data-confirm") || "";
  if (confirmMessage && !window.confirm(confirmMessage)) {
    return;
  }
  if (button.getAttribute("data-chat-send") === "1") {
    sendCurrentMessage(command);
    return;
  }
  if (!dom.commandInput) {
    return;
  }
  dom.commandInput.value = command;
  dom.commandInput.focus();
  autosizeTextarea();
  state.selectedSuggestionIndex = 0;
  renderSuggestions();
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
  dom.chatThread.innerHTML = rows.map((row) => {
    const role = row.role === "user" ? "user" : "assistant";
    const avatar = role === "assistant" ? "E" : "J";
    return [
      '<article class="chat-message" data-role="' + role + '">',
      '<div class="chat-avatar">' + avatar + "</div>",
      '<div class="chat-bubble">',
      '<div class="chat-meta">',
      "<strong>" + escapeHtml(row.author || (role === "assistant" ? "Evelyn" : "정훈")) + "</strong>",
      "<span>" + escapeHtml(formatTimestamp(row.at)) + "</span>",
      "</div>",
      "<p>" + escapeHtml(row.text || "") + "</p>",
      "</div>",
      "</article>",
    ].join("");
  }).join("");
  if (!preserveScroll || wasNearBottom) {
    dom.chatThread.scrollTop = dom.chatThread.scrollHeight;
    return;
  }
  const nextScrollHeight = dom.chatThread.scrollHeight;
  const delta = nextScrollHeight - previousScrollHeight;
  dom.chatThread.scrollTop = Math.max(0, previousScrollTop + delta);
}

const MEMORY_GRAPH_COLORS = {
  core: "#fff8ea",
  project: "#8fb7ff",
  episode: "#bda1ff",
  concept: "#77d6ca",
  procedure: "#f2b46f",
  daily: "#d7d0bd",
  note: "#d7d0bd",
};

function memoryGraphTypeColor(type) {
  return MEMORY_GRAPH_COLORS[String(type || "note").toLowerCase()] || MEMORY_GRAPH_COLORS.note;
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

function prepareMemoryGraphLayout(payload) {
  if (!payload || !Array.isArray(payload.nodes)) {
    return payload;
  }
  const width = Math.max(360, dom.memoryGraphCanvas?.clientWidth || 760);
  const height = Math.max(260, dom.memoryGraphCanvas?.clientHeight || 460);
  const centerX = width / 2;
  const centerY = height / 2;
  const rings = { core: 0.12, project: 0.28, procedure: 0.44, concept: 0.58, episode: 0.72, daily: 0.86 };
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
    const ring = rings[type] || 0.66;
    const angle = ((index / total) * Math.PI * 2) + (type.length * 0.31);
    const radius = Math.min(width, height) * ring * 0.5;
    node.x = Number.isFinite(node.x) ? node.x : centerX + Math.cos(angle) * radius;
    node.y = Number.isFinite(node.y) ? node.y : centerY + Math.sin(angle) * radius;
    node.vx = Number.isFinite(node.vx) ? node.vx : 0;
    node.vy = Number.isFinite(node.vy) ? node.vy : 0;
  }
  return payload;
}

function stepMemoryGraphSimulation(payload) {
  if (!payload || !payload.nodes || !payload.nodes.length) {
    return;
  }
  const canvas = dom.memoryGraphCanvas;
  const width = Math.max(360, canvas?.clientWidth || 760);
  const height = Math.max(260, canvas?.clientHeight || 460);
  const nodeById = new Map(payload.nodes.map((node) => [node.id, node]));
  for (const node of payload.nodes) {
    node.vx += ((width / 2) - node.x) * 0.0009;
    node.vy += ((height / 2) - node.y) * 0.0009;
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
    if (state.memoryGraphPointer.dragId === node.id) {
      node.x = state.memoryGraphPointer.x;
      node.y = state.memoryGraphPointer.y;
      node.vx = 0;
      node.vy = 0;
      continue;
    }
    node.vx *= 0.86;
    node.vy *= 0.86;
    node.x = Math.max(24, Math.min(width - 24, node.x + node.vx));
    node.y = Math.max(24, Math.min(height - 24, node.y + node.vy));
  }
}

function drawMemoryGraph(payload) {
  const canvas = dom.memoryGraphCanvas;
  if (!canvas) {
    return;
  }
  const rect = canvas.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  const width = Math.max(360, Math.floor(rect.width || canvas.clientWidth || 760));
  const height = Math.max(260, Math.floor(rect.height || canvas.clientHeight || 460));
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
    const radius = Math.max(7, Math.min(24, Number(node.size || 14) * 0.58));
    const selected = node.id === state.memoryGraphSelectedNodeId;
    const hovered = node.id === state.memoryGraphPointer.hoverId;
    ctx.beginPath();
    ctx.arc(node.x, node.y, radius + (selected ? 5 : (hovered ? 3 : 0)), 0, Math.PI * 2);
    ctx.fillStyle = selected ? "rgba(255, 248, 234, 0.20)" : (hovered ? "rgba(255, 248, 234, 0.13)" : "rgba(255, 248, 234, 0.05)");
    ctx.fill();
    ctx.beginPath();
    ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
    ctx.fillStyle = memoryGraphTypeColor(node.type);
    ctx.fill();
    ctx.lineWidth = selected ? 2.4 : 1.2;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.72)";
    ctx.stroke();
    if (selected || hovered || node.type === "core" || node.type === "project") {
      ctx.font = "600 11px IBM Plex Sans KR, sans-serif";
      ctx.fillStyle = "rgba(255, 248, 234, 0.92)";
      const label = String(node.title || node.id || "").slice(0, 32);
      ctx.fillText(label, node.x + radius + 7, node.y + 4);
    }
  }
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
  const canvas = dom.memoryGraphCanvas;
  if (!canvas) {
    return null;
  }
  const rect = canvas.getBoundingClientRect();
  const x = clientX - rect.left;
  const y = clientY - rect.top;
  state.memoryGraphPointer.x = x;
  state.memoryGraphPointer.y = y;
  let best = null;
  let bestDist = 999999;
  for (const node of memoryGraphVisiblePayload().nodes || []) {
    const dx = node.x - x;
    const dy = node.y - y;
    const dist = Math.sqrt(dx * dx + dy * dy);
    const radius = Math.max(10, Math.min(28, Number(node.size || 14) * 0.66));
    if (dist < radius && dist < bestDist) {
      best = node;
      bestDist = dist;
    }
  }
  return best;
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
    return "숨김";
  }
  return card.confirmed ? "확인됨" : "확인 필요";
}

function renderMemoryCards(payload) {
  state.memoryCardsPayload = payload || { cards: [], counts: {} };
  const counts = state.memoryCardsPayload.counts || {};
  if (dom.memoryManagerSummary) {
    dom.memoryManagerSummary.innerHTML = [
      "<span>확인됨 <strong>" + escapeHtml(counts.confirmed || 0) + "</strong></span>",
      "<span>미확인 <strong>" + escapeHtml(counts.unconfirmed || 0) + "</strong></span>",
      "<span>고정 <strong>" + escapeHtml(counts.pinned || 0) + "</strong></span>",
    ].join("");
  }
  if (!dom.memoryCardList) {
    return;
  }
  const cards = Array.isArray(state.memoryCardsPayload.cards) ? state.memoryCardsPayload.cards : [];
  if (!cards.length) {
    dom.memoryCardList.innerHTML = '<article class="memory-card memory-card-empty">표시할 메모리 카드가 없습니다.</article>';
    return;
  }
  dom.memoryCardList.innerHTML = cards.slice(0, 12).map((card) => {
    const statusClass = card.confirmed ? "is-confirmed" : "is-unconfirmed";
    const pinLabel = card.pinned ? "고정 해제" : "고정";
    const pinAction = card.pinned ? "unpin" : "pin";
    const confirmLabel = card.confirmed ? "확인 취소" : "확인";
    const confirmAction = card.confirmed ? "unconfirm" : "confirm";
    return [
      '<article class="memory-card ' + statusClass + '" data-memory-note-id="' + escapeHtml(card.id) + '">',
      '<div class="memory-card-head">',
      '<div>',
      '<span class="memory-card-category">' + escapeHtml(card.category || card.type || "기억") + "</span>",
      "<strong>" + escapeHtml(card.title || "제목 없음") + "</strong>",
      "</div>",
      '<span class="memory-card-status">' + escapeHtml(memoryCardStatusLabel(card)) + "</span>",
      "</div>",
      '<p class="memory-card-preview">' + escapeHtml(card.preview || "내용 없음") + "</p>",
      '<div class="memory-card-meta">',
      "<span>" + escapeHtml(card.path || "") + "</span>",
      card.pinned ? "<span>고정됨</span>" : "",
      card.confirmedAt ? "<span>" + escapeHtml(card.confirmedAt.slice(0, 10)) + "</span>" : "",
      "</div>",
      '<div class="memory-card-actions">',
      '<button type="button" data-memory-action="' + confirmAction + '">' + confirmLabel + "</button>",
      '<button type="button" data-memory-action="' + pinAction + '">' + pinLabel + "</button>",
      '<button type="button" data-memory-action="edit">수정</button>',
      '<button type="button" class="is-danger" data-memory-action="hide">숨김</button>',
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
    dom.memoryManagerStatus.textContent = "메모리 카드를 불러오는 중입니다.";
  }
  try {
    const payload = await fetchApi("/api/control-page/memory?limit=80");
    state.memoryCardsLastLoadedAt = Date.now();
    renderMemoryCards(payload);
    if (dom.memoryManagerStatus) {
      dom.memoryManagerStatus.textContent = "확인 여부와 고정 상태가 저장됩니다.";
    }
  } catch (error) {
    if (dom.memoryManagerStatus) {
      dom.memoryManagerStatus.textContent = "메모리 카드를 불러오지 못했습니다: " + error.message;
    }
  } finally {
    state.memoryCardsLoading = false;
  }
}

async function updateMemoryCardAction(noteId, action, extra = {}) {
  if (!noteId || !action) {
    return;
  }
  if (dom.memoryManagerStatus) {
    dom.memoryManagerStatus.textContent = "메모리 상태를 저장하는 중입니다.";
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
  } catch (error) {
    if (dom.memoryManagerStatus) {
      dom.memoryManagerStatus.textContent = "저장 실패: " + error.message;
    }
  }
}

function handleMemoryCardAction(button) {
  const card = button.closest("[data-memory-note-id]");
  const noteId = card ? card.getAttribute("data-memory-note-id") : "";
  const action = button.getAttribute("data-memory-action") || "";
  if (!noteId || !action) {
    return;
  }
  if (action === "hide" && !window.confirm("이 기억을 메모리 화면에서 숨길까요?")) {
    return;
  }
  if (action === "edit") {
    const currentTitle = card.querySelector(".memory-card-head strong")?.textContent || "";
    const currentBody = card.querySelector(".memory-card-preview")?.textContent || "";
    const nextBody = window.prompt("수정할 기억 내용을 입력하세요.", currentBody);
    if (nextBody === null) {
      return;
    }
    updateMemoryCardAction(noteId, "edit", { title: currentTitle, body: nextBody });
    return;
  }
  updateMemoryCardAction(noteId, action);
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
  dom.memoryGraphFilter.innerHTML = types.map((type) => {
    const active = state.memoryGraphFilterType === type ? " is-active" : "";
    const label = type === "all" ? "All" : type;
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
  if (!text.includes("Evelyn 상태")) {
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
  const chatWidth = Math.min(500, Math.max(400, Math.round(viewportWidth * 0.27)));
  const avatarWidth = Math.min(860, Math.max(560, viewportWidth - chatWidth - 88));
  const avatarHeight = Math.min(660, viewportHeight - 104);
  const chatHeight = Math.min(720, viewportHeight - 104);
  const avatarX = 24;
  const chatX = Math.max(24, Math.min(viewportWidth - chatWidth - 24, avatarX + avatarWidth + 24));
  const defaults = {
    runtime: { width: 390, height: 620, x: 32, y: 64 },
    diagnostics: { width: 430, height: 560, x: 88, y: 112 },
    avatar: { width: avatarWidth, height: avatarHeight, x: avatarX, y: 32 },
    chat: { width: chatWidth, height: chatHeight, x: chatX, y: 32 },
    memory: { width: Math.min(860, Math.max(620, Math.round(viewportWidth * 0.52))), height: Math.min(720, viewportHeight - 96), x: 64, y: 56 },
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
    setApiBootProgress(100, "API 연결 완료");
    hideApiBootProgressSoon();
  }
  state.appState = payload;
  state.commands = payload.commands || [];
  state.allCommands = mergedCommandCatalog(CONTROL_PAGE_COMMAND_CATALOG, state.commands, payload.allCommands || []);

  const voice = payload.voice || {};
  const runtime = payload.runtime || {};
  const services = runtime.services || {};
  const minecraft = payload.minecraft || {};
  const guild = payload.guild || {};
  applyPanelCommands(runtime.controlPagePanels);
  const ui = resolveUiMode(payload);
  const minecraftActive = ui.mode === "minecraft";
  const minecraftIdleSummary = minecraft.idleSummary || "지금은 Minecraft 플레이 전입니다. 연결되면 위젯이 자동으로 나타납니다.";
  const minecraftSnapshotStale = Boolean(minecraft.snapshotStale);
  const minecraftStartupExpected = Boolean(minecraftActive || minecraft.running || ui.submode === "voyager-warmup");
  const hasIssue = Boolean(
    minecraft.lastError
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
    dom.topbarStatusLine.textContent = cleanDisplayText(payload.statusText, "운영 상태를 확인하는 중입니다.");
  }

  if (dom.avatarStatusCopy) {
    if (voice.speaking) {
      dom.avatarStatusCopy.textContent = cleanDisplayText(voice.ttsTargetName, "현재 대상") + "에게 말하는 중입니다.";
    } else if (ui.submode === "voyager-warmup") {
      dom.avatarStatusCopy.textContent = "Voyager는 올라와 있고, Minecraft 세션 연결을 마저 기다리는 중입니다.";
    } else {
      dom.avatarStatusCopy.textContent = "음성 출력 중이 아니면 차분한 idle 상태를 유지합니다.";
    }
  }

  if (dom.avatarShell) {
    dom.avatarShell.classList.toggle("is-speaking", Boolean(voice.speaking));
  }
  if (dom.ttsTargetName) {
    dom.ttsTargetName.textContent = voice.speaking ? (voice.ttsTargetName || "없음") : "없음";
  }
  if (dom.voicePresencePill) {
    dom.voicePresencePill.textContent = ui.submode === "offline"
      ? "오프라인"
      : (ui.submode === "voyager-warmup"
        ? "준비 중"
        : (voice.speaking ? "대화 중" : (voice.listening ? "듣는 중" : "대기 중")));
    setStateClasses(dom.voicePresencePill, [presencePillState(ui, voice)], ["is-idle", "is-active", "is-warmup", "is-issue", "is-offline"]);
  }
  if (voice.speaking) {
    avatarTalkStart();
  } else {
    avatarTalkStop();
  }

  if (dom.operatorRuntimeTitle) {
    dom.operatorRuntimeTitle.textContent = ui.submode === "voyager-warmup"
      ? "Voyager 준비 중"
      : (voice.speaking ? "Evelyn 응답 중" : (voice.listening ? "Evelyn 듣는 중" : "Evelyn 대기 중"));
  }
  if (dom.operatorRuntimeSubcopy) {
    dom.operatorRuntimeSubcopy.textContent = ui.submode === "voyager-warmup"
      ? "Minecraft HUD로 완전히 전환되기 전 단계입니다."
      : "음성, TTS, LLM, Voyager 상태를 여기서 먼저 확인합니다.";
  }
  if (dom.operatorRuntimeNote) {
    dom.operatorRuntimeNote.textContent = ui.submode === "voyager-warmup"
      ? "세션이 실제로 붙으면 화면이 Minecraft mode로 전환됩니다."
      : "Minecraft를 아직 열지 않았다면 기본 운영 상태와 다음 추천 액션을 먼저 보여줍니다.";
  }
  if (dom.operatorRuntimeDot) {
    dom.operatorRuntimeDot.classList.toggle("is-offline", ui.submode === "offline");
    dom.operatorRuntimeDot.classList.toggle("is-warmup", ui.submode === "voyager-warmup");
  }
  if (dom.operatorStatChannel) {
    dom.operatorStatChannel.textContent = cleanDisplayText(voice.channelName, "없음");
  }
  if (dom.operatorStatMode) {
    dom.operatorStatMode.textContent = cleanDisplayText(ui.submode || ui.mode, "default");
  }
  if (dom.operatorStatTts) {
    dom.operatorStatTts.textContent = String(runtime.ttsBacklog || 0);
  }
  if (dom.operatorStatLlm) {
    dom.operatorStatLlm.textContent = String(runtime.inflightLlmRequests || 0);
  }
  const voicePipeline = runtime.voicePipeline || {};
  if (dom.voicePipelineQueue) {
    dom.voicePipelineQueue.textContent = `${voicePipeline.queueDepth || 0}/${voicePipeline.queueMax || 0}`;
  }
  if (dom.voicePipelineStt) {
    const cooldown = Number(voicePipeline.sttCooldownRemainingSec || 0);
    dom.voicePipelineStt.textContent = voicePipeline.sttBusy ? "busy" : (cooldown > 0 ? `${cooldown.toFixed(1)}s` : "idle");
  }
  if (dom.voicePipelineTts) {
    dom.voicePipelineTts.textContent = `${Math.round(Number(voicePipeline.ttsFirstAudioMsP95 || 0))}ms`;
  }
  if (dom.voicePipelineDrops) {
    const drops = Number(voicePipeline.queueFullDropCount || 0) + Number(voicePipeline.queueStaleDropCount || 0);
    dom.voicePipelineDrops.textContent = String(drops);
  }
  const localMic = runtime.localMic || {};
  const voiceInputMode = String(localMic.inputMode || "auto").toLowerCase();
  if (dom.voiceInputModeButtons) {
    dom.voiceInputModeButtons.forEach((button) => {
      const mode = String(button.getAttribute("data-voice-input-mode") || "auto").toLowerCase();
      button.setAttribute("aria-pressed", String(mode === voiceInputMode));
    });
  }

  if (dom.minecraftRuntimeTitle) {
    dom.minecraftRuntimeTitle.textContent = minecraftActive
      ? "Minecraft 플레이 중"
      : (minecraft.running ? "Minecraft 연결 대기" : "Minecraft 비활성");
  }
  if (dom.minecraftRuntimeSubcopy) {
    const liveCopy = minecraft.task || minecraft.goal || minecraft.progress || "실시간 플레이 상태를 추적하는 중입니다.";
    dom.minecraftRuntimeSubcopy.textContent = minecraftActive
      ? (minecraftSnapshotStale ? (liveCopy + " 실시간 갱신이 잠시 늦어지고 있습니다.") : liveCopy)
      : minecraftIdleSummary;
  }
  if (dom.minecraftRuntimeDot) {
    dom.minecraftRuntimeDot.classList.toggle("is-offline", !minecraftActive);
  }
  if (dom.minecraftIdleNote) {
    dom.minecraftIdleNote.textContent = minecraftIdleSummary;
  }

  if (dom.statCurrentTask) dom.statCurrentTask.textContent = minecraft.task || "없음";
  if (dom.statStage) dom.statStage.textContent = minecraft.stage || "없음";
  if (dom.statUniqueItems) dom.statUniqueItems.textContent = minecraft.uniqueItemCount ?? "-";
  if (dom.statTravelDistance) dom.statTravelDistance.textContent = formatDistance(minecraft.travelDistanceBlocks);
  if (dom.statHealthHunger) dom.statHealthHunger.textContent = formatHealthHunger(minecraft.health, minecraft.hunger);
  if (dom.statSkillLibrary) dom.statSkillLibrary.textContent = minecraft.skillLibrarySize ?? "-";

  renderActivityRows(minecraft.recentActivity || []);

  if (dom.operationsEyebrow) {
    dom.operationsEyebrow.textContent = minecraftActive ? "LIVE TELEMETRY" : "OPERATIONS FEED";
  }
  if (dom.operationsTitle) {
    dom.operationsTitle.textContent = minecraftActive ? "Minecraft 흐름" : "운영 흐름";
  }
  if (dom.operationsSubcopy) {
    dom.operationsSubcopy.textContent = minecraftActive
      ? "목표, 최근 활동, 서비스 지표를 live session 기준으로 묶어 보여줍니다."
      : (ui.submode === "voyager-warmup"
        ? "지금은 운영 대시보드를 유지한 채 Minecraft 세션 연결이 실제로 붙는지 기다립니다."
        : "기본 상태에서는 Evelyn 운영 흐름과 서비스 상태를 먼저 보여줍니다.");
  }

  if (dom.systemSummaryPill) {
    dom.systemSummaryPill.textContent = minecraftActive
      ? (minecraftSnapshotStale ? "플레이 중 · 지연" : "플레이 중")
      : (ui.submode === "voyager-warmup"
        ? "연결 준비 중"
        : (ui.submode === "offline" ? "오프라인" : (hasIssue ? "주의 필요" : "기본 준비됨")));
    dom.systemSummaryPill.title = services.codexError || services.voyagerError || "";
    setStateClasses(dom.systemSummaryPill, [summaryPillState(ui, hasIssue)], ["is-idle", "is-active", "is-warmup", "is-issue", "is-offline"]);
  }

  setMeter(
    dom.meterVoyager,
    dom.meterVoyagerLabel,
    meterLevel(minecraftActive ? "active" : (minecraft.running ? "warm" : "idle")),
    minecraftActive ? "connected" : (minecraft.running ? "starting" : "idle")
  );
  setMeter(
    dom.meterVoice,
    dom.meterVoiceLabel,
    meterLevel(voice.listening ? "active" : "idle"),
    voice.listening ? "listening" : "idle"
  );
  setMeter(
    dom.meterTts,
    dom.meterTtsLabel,
    voice.speaking ? 100 : 8,
    voice.speaking ? (voice.ttsTargetName || "active") : "없음"
  );
  setMeter(
    dom.meterLlm,
    dom.meterLlmLabel,
    Math.min(100, (runtime.inflightLlmRequests || 0) * 32),
    String(runtime.inflightLlmRequests || 0) + " inflight"
  );

  if (dom.guildName) {
    dom.guildName.textContent = guild.name || "Guild 미연결";
  }
  if (dom.objectiveGoal) dom.objectiveGoal.textContent = minecraft.goal || "없음";
  if (dom.objectiveProgress) dom.objectiveProgress.textContent = minecraft.progress || "진행 메시지 없음";
  if (dom.objectiveStage) dom.objectiveStage.textContent = minecraft.stage || "없음";
  if (dom.objectiveTaskStage) dom.objectiveTaskStage.textContent = minecraft.taskStage || "없음";
  if (dom.positionBlock) dom.positionBlock.textContent = minecraft.position || "미확인";
  if (dom.inventorySummary) dom.inventorySummary.textContent = minecraft.inventorySummary || "인벤토리 정보 없음";
  renderInventoryWidget(
    minecraft.inventorySummary,
    minecraft.inventoryTop || [],
    minecraft.inventorySlots || [],
    minecraft.inventoryUsedSlots,
    minecraft.uniqueItemCount,
  );
  if (!minecraftActive) {
    setInventoryWidgetOpen(false);
  }
  if (dom.commandInput) {
    dom.commandInput.placeholder = minecraftActive
      ? "Evelyn에게 메시지 보내기"
      : "메시지를 보내거나 /minecraft connect 입력";
  }
  if (dom.composerHintLeft) {
    dom.composerHintLeft.textContent = "Enter 전송";
  }
  if (dom.actionsEyebrow) {
    dom.actionsEyebrow.textContent = minecraftActive ? "MISSION ACTIONS" : "CONTROL ACTIONS";
  }
  if (dom.actionsSubcopy) {
    dom.actionsSubcopy.textContent = minecraftActive
      ? "Minecraft 미션 제어, 인벤토리 확인, 최근 대화를 한 자리에서 이어갈 수 있습니다."
      : "기본 상태에서는 운영 액션과 최근 대화를 우선 보여주고, Minecraft가 붙으면 미션 제어 요약으로 바뀝니다.";
  }
  if (dom.primaryActionTitle) {
    dom.primaryActionTitle.textContent = minecraftActive ? "미션 제어" : "바로 실행";
  }
  if (dom.supportActionTitle) {
    dom.supportActionTitle.textContent = minecraftActive ? "운영 보조" : "세부 명령";
  }
  if (dom.supportActionCaption) {
    dom.supportActionCaption.textContent = minecraftActive
      ? "미션 중에도 기본 상태 확인과 보조 제어를 바로 열 수 있습니다."
      : "자주 쓰는 상태 확인과 보조 제어입니다.";
  }

  renderControlBrief(payload);
  renderDefaultViewport(payload, ui);
  renderMinecraftOpsPanel(payload);
  renderQuickCommands();
  renderChat((payload.chat || {}).messages || [], payload.statusText || "연결 상태를 확인하는 중입니다.", { preserveScroll });
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
  const text = rawText.trim();
  if (!text || state.sending) {
    return;
  }
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
    renderChat([], "메시지 전송에 실패했습니다: " + error.message);
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

if (dom.quickCommandRow) {
  dom.quickCommandRow.addEventListener("click", (event) => {
    const button = event.target.closest("[data-chat-command]");
    if (!button) {
      return;
    }
    handleChatCommandTrigger(button);
  });
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
    const node = nearestMemoryGraphNode(event.clientX, event.clientY);
    state.memoryGraphPointer.hoverId = node ? node.id : "";
    dom.memoryGraphCanvas.style.cursor = state.memoryGraphPointer.dragId ? "grabbing" : (node ? "pointer" : "default");
  });
  dom.memoryGraphCanvas.addEventListener("pointerdown", (event) => {
    const node = nearestMemoryGraphNode(event.clientX, event.clientY);
    if (!node) {
      state.memoryGraphSelectedNodeId = "";
      renderMemoryGraphDetail(null);
      return;
    }
    state.memoryGraphSelectedNodeId = node.id;
    state.memoryGraphPointer.dragId = node.id;
    dom.memoryGraphCanvas.setPointerCapture?.(event.pointerId);
    renderMemoryGraphDetail(node);
  });
  dom.memoryGraphCanvas.addEventListener("pointerup", (event) => {
    state.memoryGraphPointer.dragId = "";
    dom.memoryGraphCanvas.releasePointerCapture?.(event.pointerId);
  });
  dom.memoryGraphCanvas.addEventListener("pointerleave", () => {
    state.memoryGraphPointer.hoverId = "";
    state.memoryGraphPointer.dragId = "";
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
ensureApiWaitingTicker();
refreshState();
schedulePolling();
