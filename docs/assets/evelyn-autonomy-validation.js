(function () {
  "use strict";

  const mount = document.getElementById("autonomyValidationMount");
  const startButton = document.getElementById("autonomyValidationStartButton");
  const guildInput = document.getElementById("autonomyValidationGuildId");
  if (!mount || !startButton || !guildInput) return;

  const state = { session: null, busy: false, timer: null };
  const terminalStates = new Set(["passed", "failed", "aborted"]);
  const activeStates = new Set(["preflight", "running", "cleanup_required"]);
  const stepCopy = {
    "01-explicit-grant": ["Assistant 승인", "Discord에서 소유자가 자율시작을 실행한 뒤 grant 증거를 기다립니다."],
    "02-assistant-action-authorized": ["실행 전 승인", "동일 grant와 실행별 actionRunId의 assistant action 판정을 기다립니다."],
    "03-assistant-outcome-verified": ["검증된 Assistant 결과", "같은 actionRunId, 현재 승인과 정확한 evidence code를 가진 결과만 인정합니다."],
    "04-world-lease-lifecycle": ["Minecraft lease", "소유자가 명시적으로 연결한 뒤 lease와 runtime start 증거를 기다립니다."],
    "05-world-postcondition": ["Minecraft 실제 효과", "goal echo가 아닌 explicit postcondition 투영 증거를 기다립니다."],
    "06-revoke-and-stop": ["권한 회수와 정지", "소유자가 자율정지·Minecraft 종료를 실행한 뒤 inactive 증거를 기다립니다."],
  };
  const codeCopy = {
    autonomy_authorization_status_missing: "자율행동 승인 상태가 없습니다.",
    autonomy_authorization_status_invalid: "자율행동 승인 상태 계약이 손상됐습니다.",
    autonomy_authorization_audit_unavailable: "승인 감사 원장을 사용할 수 없습니다.",
    active_authorization_present: "이미 활성 grant가 있어 깨끗한 기준점이 아닙니다.",
    minecraft_world_lease_status_missing: "Minecraft lease 상태가 없습니다.",
    minecraft_world_lease_status_invalid: "Minecraft lease 상태 계약이 손상됐습니다.",
    minecraft_world_lease_audit_unavailable: "Minecraft lease 감사 원장을 사용할 수 없습니다.",
    active_world_lease_present: "이미 활성 lease가 있어 깨끗한 기준점이 아닙니다.",
    minecraft_autonomy_route_unwired: "승인된 Minecraft action 실행 경로가 production에 연결되지 않았습니다.",
    minecraft_postcondition_observer_unavailable: "Minecraft explicit postcondition 관찰 증거가 없습니다.",
    cleanup_required: "검증 중 생긴 grant 또는 lease를 수동으로 회수해야 합니다.",
    cleanup_state_unknown: "외부 grant·lease 상태를 확인할 수 없습니다. 검증기는 자동 정리를 실행하지 않습니다.",
    session_expired: "검증 세션이 만료됐습니다.",
    authorization_status_missing: "자율행동 승인 상태가 없습니다.",
    authorization_status_corrupt: "자율행동 승인 상태를 읽을 수 없습니다.",
    authorization_status_schema_mismatch: "자율행동 승인 상태 계약이 일치하지 않습니다.",
    authorization_audit_unavailable: "자율행동 승인 감사 원장을 사용할 수 없습니다.",
    world_lease_status_missing: "Minecraft lease 상태가 없습니다.",
    world_lease_status_corrupt: "Minecraft lease 상태를 읽을 수 없습니다.",
    world_lease_status_schema_mismatch: "Minecraft lease 상태 계약이 일치하지 않습니다.",
    world_lease_audit_unavailable: "Minecraft lease 감사 원장을 사용할 수 없습니다.",
    validation_audit_unavailable: "검증 자체 감사 원장을 쓸 수 없어 세션을 실패로 닫았습니다.",
  };

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = String(text);
    return element;
  }

  async function api(path, options) {
    if (typeof window.fetchApi === "function") return window.fetchApi(path, options);
    const request = Object.assign({}, options || {});
    request.headers = Object.assign({}, request.headers || {});
    if (request.method && request.method !== "GET") {
      const sessionResponse = await fetch("/api/control-page/session", { cache: "no-store" });
      const sessionPayload = await sessionResponse.json();
      request.headers[sessionPayload.csrfHeader || "X-Evelyn-CSRF-Token"] = sessionPayload.csrfToken;
      request.headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, request);
    const payload = await response.json();
    if (!response.ok) {
      const error = new Error(payload.error || `HTTP ${response.status}`);
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function fixedCode(value) {
    const code = typeof value === "string" ? value : String((value && value.code) || "unknown");
    return codeCopy[code] || code;
  }

  function appendNotices(parent, values, kind) {
    (Array.isArray(values) ? values : []).forEach((value) => {
      parent.appendChild(node("div", `validation-wizard-notice is-${kind}`, fixedCode(value)));
    });
  }

  function renderStep(parent, session) {
    const step = session.currentStep || {};
    if (!step.id) return;
    const copy = stepCopy[step.id] || [step.id, "서버의 content-free 증거를 기다립니다."];
    const card = node("article", "validation-wizard-step");
    const head = node("div", "validation-wizard-step-head");
    head.appendChild(node("strong", "", copy[0]));
    const pill = node("span", "validation-wizard-pill", step.status || session.state || "unknown");
    pill.dataset.state = step.status || session.state || "unknown";
    head.appendChild(pill);
    card.appendChild(head);
    card.appendChild(node("p", "validation-wizard-meta", copy[1]));
    card.appendChild(node("span", "validation-wizard-meta", `시도 ${Number(step.attempt || 1)} / 3`));

    const actions = node("div", "validation-wizard-actions");
    appendNotices(card, step.errors, "blocker");
    if (step.manualAcknowledgementRequired === true) {
      const confirm = node("button", "is-primary", "수동 단계 관찰 시작");
      confirm.type = "button";
      confirm.dataset.autonomyConfirm = "1";
      confirm.disabled = session.state === "preflight" && (session.preflightBlockers || []).length > 0;
      actions.appendChild(confirm);
    }
    const retry = node("button", "", "단계 재시도");
    retry.type = "button";
    retry.dataset.autonomyRetry = "1";
    retry.disabled = step.status !== "failed";
    actions.appendChild(retry);
    const abort = node("button", "is-danger", "세션 중단");
    abort.type = "button";
    abort.dataset.autonomyAbort = "1";
    actions.appendChild(abort);
    card.appendChild(actions);
    parent.appendChild(card);
  }

  function renderSummary(parent, session) {
    const summary = session.summary || {};
    const total = Number(summary.stepsTotal || summary.total || 0);
    const passed = Number(summary.stepsPassed || summary.passed || 0);
    const percent = total > 0 ? Math.max(0, Math.min(100, Math.round((passed / total) * 100))) : 0;
    const card = node("article", "validation-wizard-summary");
    const row = node("div", "validation-wizard-summary-row");
    row.appendChild(node("strong", "", "검증 요약"));
    const pill = node("span", "validation-wizard-pill", session.state || "idle");
    pill.dataset.state = session.state || "idle";
    row.appendChild(pill);
    card.appendChild(row);
    const progress = node("div", "validation-wizard-progress");
    const bar = node("i");
    bar.style.width = `${percent}%`;
    progress.appendChild(bar);
    card.appendChild(progress);
    card.appendChild(node("span", "validation-wizard-meta", `${passed} / ${total} 단계 · 자동 실행 false · 요청 큐 쓰기 false`));
    parent.appendChild(card);
  }

  function renderCleanup(parent, session) {
    const cleanup = session.cleanupStep;
    if (!cleanup || !cleanup.id) return;
    const card = node("article", "validation-wizard-step");
    const head = node("div", "validation-wizard-step-head");
    head.appendChild(node("strong", "", "정리 증거"));
    const pill = node("span", "validation-wizard-pill", cleanup.status || "pending");
    pill.dataset.state = cleanup.status || "pending";
    head.appendChild(pill);
    card.appendChild(head);
    card.appendChild(node("p", "validation-wizard-meta", "grant 회수, 같은 lease의 revoke와 verified stop을 모두 관찰해야 합니다."));
    Object.entries(cleanup.requirements || {}).forEach(([code, satisfied]) => {
      card.appendChild(node("span", "validation-wizard-meta", `${code}: ${satisfied === true ? "observed" : "pending"}`));
    });
    parent.appendChild(card);
  }

  function render() {
    const session = state.session || { state: "idle", blockers: [], warnings: [], summary: {} };
    startButton.disabled = state.busy || !terminalStates.has(session.state) && session.state !== "idle";
    guildInput.disabled = startButton.disabled;
    startButton.textContent = state.busy ? "처리 중" : "Dry preflight";
    const fragment = document.createDocumentFragment();
    const safety = node("div", "validation-wizard-notice");
    safety.appendChild(node("strong", "", "관찰 전용 안전 경계"));
    safety.appendChild(node("span", "validation-wizard-meta", "automaticExecution=false · requestQueueWrites=false · 원문 goal/chat/위치/인벤토리 저장 없음"));
    fragment.appendChild(safety);
    const capabilityValues = Object.values(session.capabilities || {});
    const blockerCodes = new Map();
    const warningCodes = new Map();
    [
      ...(session.blockers || []),
      ...(session.preflightBlockers || []),
      ...((session.preflight && session.preflight.blockers) || []),
    ]
      .forEach((value) => blockerCodes.set(fixedCode(value), value));
    capabilityValues.forEach((capability) => {
      (capability && capability.blockers || [])
        .forEach((value) => blockerCodes.set(fixedCode(value), value));
      (capability && capability.warnings || [])
        .forEach((value) => warningCodes.set(fixedCode(value), value));
    });
    appendNotices(fragment, Array.from(blockerCodes.values()), "blocker");
    appendNotices(fragment, Array.from(warningCodes.values()), "warning");
    if (session.summary && session.summary.cleanupRequired === true) {
      appendNotices(fragment, ["cleanup_required"], "blocker");
    } else if (session.summary && session.summary.cleanupStateUnknown === true) {
      appendNotices(fragment, ["cleanup_state_unknown"], "warning");
    }
    appendNotices(fragment, session.warnings, "warning");
    renderStep(fragment, session);
    renderCleanup(fragment, session);
    renderSummary(fragment, session);
    mount.replaceChildren(fragment);
  }

  function scheduleRefresh() {
    if (state.timer) window.clearTimeout(state.timer);
    state.timer = window.setTimeout(refresh, state.session && activeStates.has(state.session.state) ? 1000 : 5000);
  }

  async function refresh() {
    try {
      const payload = await api("/api/control-page/autonomy-validation", { cache: "no-store" });
      state.session = payload.session || payload;
      render();
    } catch (error) {
      mount.replaceChildren(node("div", "validation-wizard-notice is-blocker", `검증 상태를 읽지 못했습니다: ${error.message}`));
    } finally {
      scheduleRefresh();
    }
  }

  async function mutate(path, payload) {
    state.busy = true;
    render();
    try {
      const result = await api(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      state.session = result.session || state.session;
      render();
      return result;
    } catch (error) {
      if (error.payload && error.payload.session) state.session = error.payload.session;
      window.alert(error.message);
      render();
      return null;
    } finally {
      state.busy = false;
      scheduleRefresh();
    }
  }

  startButton.addEventListener("click", async () => {
    const guildId = guildInput.value.trim();
    if (!/^[1-9][0-9]{0,19}$/.test(guildId)) {
      window.alert("검증할 Discord guild ID를 입력하세요.");
      return;
    }
    await mutate("/api/control-page/autonomy-validation/start", {
      suite: "autonomy-p0.v1",
      guildId,
      dryRun: true,
    });
  });

  mount.addEventListener("click", async (event) => {
    const session = state.session || {};
    const step = session.currentStep || {};
    if (event.target.closest("[data-autonomy-confirm]")) {
      if (!window.confirm("자동 실행 없이 기존 Discord·Minecraft 증거만 관찰할까요?")) return;
      await mutate("/api/control-page/autonomy-validation/confirm", {
        sessionId: session.sessionId,
        stepId: step.id,
        attempt: step.attempt,
        userConfirmed: true,
      });
    } else if (event.target.closest("[data-autonomy-retry]")) {
      await mutate("/api/control-page/autonomy-validation/retry", {
        sessionId: session.sessionId,
        stepId: step.id,
        attempt: step.attempt,
      });
    } else if (event.target.closest("[data-autonomy-abort]")) {
      if (!window.confirm("세션을 중단할까요? 활성 grant/lease가 있으면 cleanup_required로 남습니다.")) return;
      await mutate("/api/control-page/autonomy-validation/abort", { sessionId: session.sessionId });
    }
  });

  render();
  refresh();
})();
