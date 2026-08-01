(function () {
  "use strict";

  const mount = document.getElementById("voiceValidationMount");
  const startButton = document.getElementById("voiceValidationStartButton");
  if (!mount || !startButton) return;

  const state = {
    session: null,
    busy: false,
    surfaces: new Set(["local", "discord"]),
    timer: null,
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  async function api(path, options) {
    if (typeof window.fetchApi === "function") {
      return window.fetchApi(path, options);
    }
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
      const error = new Error(payload.message || payload.error || `HTTP ${response.status}`);
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function eventCount(step, name) {
    return Number((step && step.events && step.events[name]) || 0);
  }

  function capabilityCard(id, label, capability) {
    const value = capability || { state: "unknown", blockers: [], warnings: [], repairActions: [] };
    const consent = value.consent || {};
    const blockers = (value.blockers || [])
      .map((item) => `<span class="voice-validation-blocker">${escapeHtml(item.message || item.code)}</span>`)
      .join("");
    const warnings = (value.warnings || [])
      .map((item) => `<span class="voice-validation-warning">${escapeHtml(item.message || item.code)}</span>`)
      .join("");
    const repairs = (value.repairActions || [])
      .map((action) => {
        const manual = action.manualCommand
          ? `<span class="voice-validation-meta">${escapeHtml(action.manualCommand)}</span>`
          : "";
        if (action.consent) {
          return `<button class="is-primary" type="button" data-voice-consent-grant="1">${escapeHtml(action.label || action.actionId)}</button>${manual}`;
        }
        return `<button type="button" data-voice-repair="${escapeHtml(action.actionId)}" data-service-id="${escapeHtml(action.serviceId)}">${escapeHtml(action.label || action.actionId)}</button>${manual}`;
      })
      .join("");
    const consentStatus = id === "voiceLocal"
      ? consent.active
        ? [
            '<div class="voice-validation-consent is-active">',
            `<span>검증용 마이크 동의 활성 · 약 ${Math.max(1, Math.ceil(Number(consent.remainingSec || 0) / 60))}분 남음</span>`,
            '<button class="is-danger" type="button" data-voice-consent-revoke="1">마이크 권한 철회</button>',
            "</div>",
          ].join("")
        : '<span class="voice-validation-meta">마이크는 기본 OFF이며 명시적 동의 후에만 검증 동안 켜집니다.</span>'
      : "";
    return [
      `<article class="voice-validation-capability" data-capability="${id}">`,
      '<div class="voice-validation-capability-head">',
      `<strong>${escapeHtml(label)}</strong>`,
      `<span class="voice-validation-pill" data-state="${escapeHtml(value.state)}">${escapeHtml(value.state)}</span>`,
      "</div>",
      blockers || warnings || '<span class="voice-validation-meta">필수 의존성이 준비됐습니다.</span>',
      consentStatus,
      repairs ? `<div class="voice-validation-actions">${repairs}</div>` : "",
      "</article>",
    ].join("");
  }

  function validationEvent(label, done) {
    return `<span class="voice-validation-event${done ? " is-done" : ""}">${escapeHtml(label)}</span>`;
  }

  function renderStep(session) {
    const step = session.currentStep || {};
    if (!step.id || session.state !== "running") return "";
    const replyEvidence = step.kind === "barge_source"
      ? validationEvent("답변 시작", eventCount(step, "reply_started") === 1)
      : validationEvent("답변 완료", eventCount(step, "reply_final") === 1);
    const events = [
      validationEvent("STT 일치", Boolean(step.match && step.match.matched)),
      validationEvent("턴 수락", eventCount(step, "turn_accepted") === 1),
      replyEvidence,
      validationEvent("재생 시작", eventCount(step, "playback_started") === 1),
      validationEvent("재생 완료", eventCount(step, "playback_completed") === 1),
      validationEvent("재생 취소", eventCount(step, "playback_cancelled") === 1),
    ].join("");
    const heardApplicable = step.kind === "normal" || step.kind === "barge_interrupt";
    const canConfirm = heardApplicable && eventCount(step, "playback_completed") === 1;
    const canRetry = step.status === "failed";
    const silenceStarted = Number(step.silenceStartedAt || 0);
    const silenceRemaining = step.kind === "silence"
      ? Math.max(0, Number(step.silenceSec || 15) - Math.floor(Date.now() / 1000 - silenceStarted))
      : 0;
    const prompt = step.kind === "silence"
      ? `<p class="voice-validation-prompt">${silenceRemaining}초 동안 말하지 마세요.</p>`
      : `<p class="voice-validation-prompt">${escapeHtml(step.prompt)}</p>`;
    const interrupt = step.interruptPrompt
      ? `<p class="voice-validation-prompt voice-validation-interrupt">재생이 시작되면 말하기: ${escapeHtml(step.interruptPrompt)}</p>`
      : "";
    const matchMeta = step.match
      ? `<span class="voice-validation-meta">문자 유사도 ${Math.round(Number(step.match.similarity || 0) * 100)}% · 키워드 ${Math.round(Number(step.match.keywordRatio || 0) * 100)}%</span>`
      : '<span class="voice-validation-meta">STT 결과 대기 중</span>';
    return [
      '<article class="voice-validation-step">',
      '<div class="voice-validation-progress-head">',
      `<strong>${escapeHtml(step.surface)} · ${escapeHtml(step.id)}</strong>`,
      `<span class="voice-validation-pill" data-state="${escapeHtml(step.status || session.state)}">시도 ${escapeHtml(step.attempt || 1)}/3</span>`,
      "</div>",
      prompt,
      interrupt,
      matchMeta,
      `<div class="voice-validation-events">${events}</div>`,
      '<div class="voice-validation-actions">',
      heardApplicable ? `<button class="is-primary" type="button" data-voice-confirm="1" ${canConfirm ? "" : "disabled"}>실제로 들렸음</button>` : "",
      `<button type="button" data-voice-retry="1" ${canRetry ? "" : "disabled"}>단계 재시도</button>`,
      `<button class="is-danger" type="button" data-voice-abort="1">세션 중단</button>`,
      "</div>",
      "</article>",
    ].join("");
  }

  function renderSummary(session) {
    const summary = session.summary || {};
    const localLatency = (summary.latency && summary.latency.local) || {};
    const discordLatency = (summary.latency && summary.latency.discord) || {};
    const total = Number(summary.stepsTotal || 0);
    const passed = Number(summary.stepsPassed || 0);
    const percent = total ? Math.round((passed / total) * 100) : 0;
    const warnings = (session.warnings || [])
      .map((warning) => `<span class="voice-validation-warning">${escapeHtml(warning.code)} · p95 ${escapeHtml(warning.p95Ms)}ms</span>`)
      .join("");
    return [
      '<article class="voice-validation-summary">',
      '<div class="voice-validation-progress-head">',
      "<strong>검증 요약</strong>",
      `<span class="voice-validation-pill" data-state="${escapeHtml(session.state)}">${escapeHtml(session.state)}</span>`,
      "</div>",
      `<div class="voice-validation-progress"><i style="width:${percent}%"></i></div>`,
      "<dl>",
      `<dt>성공 단계</dt><dd>${passed} / ${total}</dd>`,
      `<dt>Local p50 / p95</dt><dd>${escapeHtml(localLatency.p50Ms ?? "-")} / ${escapeHtml(localLatency.p95Ms ?? "-")} ms</dd>`,
      `<dt>Discord p50 / p95</dt><dd>${escapeHtml(discordLatency.p50Ms ?? "-")} / ${escapeHtml(discordLatency.p95Ms ?? "-")} ms</dd>`,
      "</dl>",
      warnings,
      "</article>",
    ].join("");
  }

  function render() {
    const session = state.session || {
      state: "idle",
      capabilities: {},
      summary: {},
      warnings: [],
    };
    const capabilities = session.capabilities || {};
    startButton.disabled = state.busy || !["idle", "passed", "failed", "aborted"].includes(session.state);
    startButton.textContent = state.busy ? "처리 중" : "검증 시작";
    const surfacePicker = session.state === "idle" || ["passed", "failed", "aborted"].includes(session.state)
      ? [
          '<div class="voice-validation-surfaces">',
          `<label><input type="checkbox" data-voice-surface="local" ${state.surfaces.has("local") ? "checked" : ""}> 로컬</label>`,
          `<label><input type="checkbox" data-voice-surface="discord" ${state.surfaces.has("discord") ? "checked" : ""}> Discord</label>`,
          "</div>",
        ].join("")
      : "";
    const preflight = session.state === "preflight"
      ? [
          '<div class="voice-validation-blocker">',
          "preflight 차단 원인을 복구한 뒤 이 세션을 중단하고 새 검증을 시작하세요.",
          '<div class="voice-validation-actions"><button class="is-danger" type="button" data-voice-abort="1">preflight 세션 중단</button></div>',
          "</div>",
        ].join("")
      : "";
    mount.innerHTML = [
      surfacePicker,
      '<div class="voice-validation-grid">',
      capabilityCard("voiceLocal", "Local voice", capabilities.voiceLocal),
      capabilityCard("voiceDiscord", "Discord voice", capabilities.voiceDiscord),
      "</div>",
      preflight,
      renderStep(session),
      renderSummary(session),
    ].join("");
  }

  async function refresh() {
    try {
      const payload = await api("/api/control-page/voice-validation", { cache: "no-store" });
      state.session = payload.session || payload;
      render();
    } catch (error) {
      mount.innerHTML = `<p class="voice-validation-blocker">검증 상태를 읽지 못했습니다: ${escapeHtml(error.message)}</p>`;
    } finally {
      scheduleRefresh();
    }
  }

  function scheduleRefresh() {
    if (state.timer) window.clearTimeout(state.timer);
    const active = state.session && ["preflight", "running"].includes(state.session.state);
    state.timer = window.setTimeout(refresh, active ? 800 : 4000);
  }

  async function mutate(path, payload) {
    state.busy = true;
    render();
    try {
      const result = await api(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      state.session = result.session || state.session;
      render();
      return result;
    } catch (error) {
      const payloadState = error.payload && error.payload.session;
      if (payloadState) state.session = payloadState;
      window.alert(error.message);
      render();
      return null;
    } finally {
      state.busy = false;
      scheduleRefresh();
    }
  }

  async function startValidation() {
    if (!state.surfaces.size) {
      window.alert("검증할 surface를 하나 이상 선택하세요.");
      return;
    }
    await mutate("/api/control-page/voice-validation/start", {
      suite: "voice-p0.v1",
      surfaces: Array.from(state.surfaces),
    });
  }

  async function repair(actionId, serviceId, button) {
    if (!actionId || actionId === "start_host_supervisor_manual") {
      window.alert("Windows에서 start_local.bat --background 를 실행하세요.");
      return;
    }
    button.disabled = true;
    try {
      const plan = await api("/api/control-page/runtime-repair/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actionId, serviceId, dryRun: true }),
      });
      if (!plan.ok || !plan.confirmToken) {
        window.alert(plan.message || plan.error || "복구 preview를 만들지 못했습니다.");
        return;
      }
      if (!window.confirm(`${plan.label || serviceId} 복구를 실행할까요?`)) return;
      await api("/api/control-page/runtime-repair/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          actionId,
          serviceId,
          confirmToken: plan.confirmToken,
          reason: "voice P0 preflight repair",
        }),
      });
      await refresh();
    } catch (error) {
      window.alert(error.message);
    } finally {
      button.disabled = false;
    }
  }

  async function grantVoiceCaptureConsent(button) {
    button.disabled = true;
    try {
      const preview = await api("/api/control-page/voice-capture-consent/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: "voice_validation_local" }),
      });
      const maxMinutes = Math.max(1, Math.ceil(Number(preview.maxConsentSec || 1800) / 60));
      const confirmed = window.confirm(
        `로컬 음성 검증을 위해 마이크를 켤까요?\n\n` +
        `검증 종료·권한 철회·Control Page 재시작 또는 최대 ${maxMinutes}분 뒤 자동으로 꺼집니다.\n` +
        "이 동의 기록에는 원문 음성이나 transcript를 저장하지 않습니다."
      );
      if (!confirmed) return;
      const result = await api("/api/control-page/voice-capture-consent/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scope: "voice_validation_local",
          confirmToken: preview.confirmToken,
        }),
      });
      if (result.validationSession) state.session = result.validationSession;
      await refresh();
    } catch (error) {
      window.alert(error.message);
    } finally {
      button.disabled = false;
    }
  }

  async function revokeVoiceCaptureConsent(button) {
    if (!window.confirm("로컬 마이크 권한을 지금 철회하고 캡처를 끌까요?")) return;
    button.disabled = true;
    try {
      await api("/api/control-page/voice-capture-consent/revoke", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "user_revoked" }),
      });
      await refresh();
    } catch (error) {
      window.alert(error.message);
    } finally {
      button.disabled = false;
    }
  }

  startButton.addEventListener("click", startValidation);
  mount.addEventListener("change", (event) => {
    const input = event.target.closest("[data-voice-surface]");
    if (!input) return;
    if (input.checked) state.surfaces.add(input.dataset.voiceSurface);
    else state.surfaces.delete(input.dataset.voiceSurface);
  });
  mount.addEventListener("click", async (event) => {
    const consentGrant = event.target.closest("[data-voice-consent-grant]");
    if (consentGrant) {
      await grantVoiceCaptureConsent(consentGrant);
      return;
    }
    const consentRevoke = event.target.closest("[data-voice-consent-revoke]");
    if (consentRevoke) {
      await revokeVoiceCaptureConsent(consentRevoke);
      return;
    }
    const repairButton = event.target.closest("[data-voice-repair]");
    if (repairButton) {
      await repair(repairButton.dataset.voiceRepair, repairButton.dataset.serviceId, repairButton);
      return;
    }
    const session = state.session || {};
    const step = session.currentStep || {};
    if (event.target.closest("[data-voice-confirm]")) {
      await mutate("/api/control-page/voice-validation/confirm", {
        sessionId: session.sessionId,
        stepId: step.id,
        attempt: step.attempt,
        heard: true,
      });
    } else if (event.target.closest("[data-voice-retry]")) {
      await mutate("/api/control-page/voice-validation/retry", {
        sessionId: session.sessionId,
        stepId: step.id,
        attempt: step.attempt,
      });
    } else if (event.target.closest("[data-voice-abort]")) {
      if (window.confirm("현재 음성 검증 세션을 중단할까요?")) {
        await mutate("/api/control-page/voice-validation/abort", {
          sessionId: session.sessionId,
        });
      }
    }
  });

  render();
  refresh();
})();
