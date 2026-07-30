(function () {
  "use strict";

  const mount = document.getElementById("uiActionMount");
  const form = document.getElementById("uiActionPreviewForm");
  const discoverButton = document.getElementById("uiActionDiscoverButton");
  const discoverySummary = document.getElementById(
    "uiActionDiscoverySummary"
  );
  const elementInput = document.getElementById("uiActionElementId");
  const postconditionInput = document.getElementById("uiActionPostcondition");
  const previewButton = document.getElementById("uiActionPreviewButton");
  const statePill = document.getElementById("uiActionStatePill");
  if (
    !mount ||
    !form ||
    !discoverButton ||
    !discoverySummary ||
    !elementInput ||
    !postconditionInput ||
    !previewButton ||
    !statePill
  ) {
    return;
  }

  const FOCUS_HANDOFF_DELAY_SEC = 5;
  const FOCUS_HANDOFF_TOKEN_MARGIN_SEC = 1;
  const FOCUS_HANDOFF_MAX_LATE_MS = 2000;
  const state = {
    busy: false,
    discovery: null,
    preview: null,
    expiryTimer: null,
    handoff: null,
    handoffTimer: null,
  };

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function setState(value) {
    const normalized = String(value || "unknown");
    statePill.textContent = normalized;
    statePill.dataset.state = normalized;
  }

  async function api(path, options) {
    if (typeof window.fetchApi === "function") {
      return window.fetchApi(path, options);
    }
    const request = Object.assign({}, options || {});
    request.headers = Object.assign({}, request.headers || {});
    if (request.method && request.method !== "GET") {
      const sessionResponse = await fetch("/api/control-page/session", {
        cache: "no-store",
      });
      const sessionPayload = await sessionResponse.json();
      request.headers[
        sessionPayload.csrfHeader || "X-Evelyn-CSRF-Token"
      ] = sessionPayload.csrfToken;
      request.headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, request);
    const payload = await response.json();
    if (!response.ok || payload.ok === false) {
      const error = new Error(
        payload.message || payload.error || `HTTP ${response.status}`
      );
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function clearPreview() {
    state.preview = null;
    if (state.expiryTimer) {
      window.clearInterval(state.expiryTimer);
      state.expiryTimer = null;
    }
    syncControls();
  }

  function clearDiscovery() {
    state.discovery = null;
    const placeholder = element(
      "option",
      "",
      "먼저 Button 찾기를 실행하세요"
    );
    placeholder.value = "";
    elementInput.replaceChildren(placeholder);
    discoverySummary.textContent =
      "먼저 대상 앱으로 전환해 실행 가능한 Button을 읽으세요.";
    syncControls();
  }

  function clearHandoff() {
    if (state.handoffTimer) {
      window.clearInterval(state.handoffTimer);
      state.handoffTimer = null;
    }
    state.handoff = null;
  }

  function selectedElementId() {
    const value = String(elementInput.value || "").trim().toLowerCase();
    return /^[0-9a-f]{20}$/.test(value) ? value : "";
  }

  function syncControls() {
    const locked = state.busy || Boolean(state.preview);
    const targets = Array.isArray(state.discovery && state.discovery.targets)
      ? state.discovery.targets
      : [];
    discoverButton.disabled = locked;
    elementInput.disabled = locked || targets.length === 0;
    postconditionInput.disabled = locked;
    previewButton.disabled = locked || !selectedElementId();
  }

  function renderDiscovery() {
    const discovery = state.discovery;
    const targets = Array.isArray(discovery && discovery.targets)
      ? discovery.targets
      : [];
    const options = [
      Object.assign(
        element(
          "option",
          "",
          targets.length
            ? "Button을 선택하세요"
            : "실행 가능한 Button이 없습니다"
        ),
        { value: "" }
      ),
    ];
    for (const target of targets) {
      const option = element(
        "option",
        "",
        `${target.name} · ${target.elementId.slice(-6)}`
      );
      option.value = target.elementId;
      options.push(option);
    }
    elementInput.replaceChildren(...options);
    if (!discovery) {
      clearDiscovery();
      syncControls();
      return;
    }
    const windowInfo = discovery.window || {};
    const windowName =
      windowInfo.title || windowInfo.className || "이름 없는 전경 창";
    discoverySummary.textContent = [
      `${windowName} · 실행 가능한 Button ${targets.length}개`,
      discovery.truncated ? "일부만 표시됨" : "",
      "발견은 실행 권한을 만들지 않음",
    ]
      .filter(Boolean)
      .join(" · ");
    syncControls();
  }

  function renderMessage(message, isError) {
    mount.replaceChildren(
      element(
        "span",
        isError ? "ui-action-error" : "ui-action-meta",
        message
      ),
      element(
        "span",
        "ui-action-policy",
        "좌표·임의 명령·자동 재시도 없음 · 대상 텍스트는 감사 로그에 저장하지 않음"
      )
    );
  }

  function remainingSeconds() {
    return Math.max(
      0,
      Math.ceil(Number(state.preview && state.preview.expiresAt) - Date.now() / 1000)
    );
  }

  function renderPreview() {
    const preview = state.preview;
    if (!preview) return;
    const target = preview.target || {};
    const card = element("article", "ui-action-preview");
    const head = element("div", "ui-action-preview-head");
    head.append(
      element("strong", "", "실행 전 확인"),
      element("span", "ui-action-pill", `${remainingSeconds()}초`)
    );

    const targetCopy = element("div", "ui-action-target");
    targetCopy.append(
      element("strong", "", target.name || "이름 없는 버튼"),
      element(
        "span",
        "ui-action-meta",
        `${target.controlType || "Button"} · ${target.elementId || ""}`
      ),
      element(
        "span",
        "ui-action-meta",
        `${target.windowTitle || "제목 없는 창"} · ${target.windowClass || "class 없음"}`
      ),
      element(
        "span",
        "ui-action-meta",
        `행동 invoke · 성공 조건 ${preview.postcondition || ""}`
      )
    );

    const actions = element("div", "ui-action-actions");
    const applyButton = element(
      "button",
      "is-primary",
      `확인 후 ${FOCUS_HANDOFF_DELAY_SEC}초 뒤 1회 실행`
    );
    applyButton.type = "button";
    applyButton.dataset.uiActionApply = "1";
    applyButton.disabled =
      state.busy ||
      remainingSeconds() <=
        FOCUS_HANDOFF_DELAY_SEC + FOCUS_HANDOFF_TOKEN_MARGIN_SEC;
    const cancelButton = element("button", "", "취소");
    cancelButton.type = "button";
    cancelButton.dataset.uiActionCancel = "1";
    cancelButton.disabled = state.busy;
    actions.append(applyButton, cancelButton);

    card.append(
      head,
      targetCopy,
      actions,
      element(
        "span",
        "ui-action-policy",
        "확인 뒤 대상 창으로 돌아갈 시간이 주어집니다. 실행 직전 같은 창·같은 요소를 재관찰합니다."
      )
    );
    mount.replaceChildren(card);
    syncControls();
  }

  function renderHandoff() {
    const handoff = state.handoff;
    if (!handoff) return;
    const applying = handoff.kind === "apply";
    const discovering = handoff.kind === "discover";
    const card = element("article", "ui-action-handoff");
    card.append(
      element(
        "strong",
        "",
        applying
          ? "승인됨 · 대상 창으로 전환"
          : discovering
            ? "Button을 읽을 창으로 전환"
            : "대상 창으로 전환"
      ),
      element(
        "span",
        "ui-action-handoff-countdown",
        `${handoff.remaining}초`
      ),
      element(
        "span",
        "ui-action-meta",
        applying
          ? "카운트가 끝나면 승인된 apply를 한 번 전송합니다. 정확히 같은 대상 창을 전경으로 두세요."
          : discovering
            ? "카운트가 끝나면 전경 창의 이름 있는 enabled Button 목록을 한 번 읽습니다. 어떤 행동도 실행하지 않습니다."
            : "카운트가 끝나면 선택한 Button의 preview를 한 번 전송합니다. 같은 대상 창을 전경으로 두세요."
      )
    );
    const actions = element("div", "ui-action-actions");
    const cancelButton = element("button", "", "카운트다운 취소");
    cancelButton.type = "button";
    cancelButton.dataset.uiActionHandoffCancel = "1";
    actions.append(cancelButton);
    card.append(
      actions,
      element(
        "span",
        "ui-action-policy",
        discovering
          ? "취소하면 요청하지 않습니다 · 발견은 실행 권한을 만들지 않습니다 · 이름 있는 enabled Button만 표시"
          : "취소하면 요청하지 않습니다 · 전경/대상 불일치는 실행 없이 token을 소모합니다 · 자동 재시도 없음"
      )
    );
    mount.replaceChildren(card);
    syncControls();
  }

  function confirmationText(preview) {
    const target = preview.target || {};
    return [
      "화면 행동을 1회 실행할까요?",
      "",
      `창: ${target.windowTitle || "제목 없음"}`,
      `대상: ${target.name || "이름 없음"} (${target.controlType || "Button"})`,
      `행동: ${preview.action || "invoke"}`,
      `성공 조건: ${preview.postcondition || ""}`,
      "",
      `${FOCUS_HANDOFF_DELAY_SEC}초 동안 대상 창으로 돌아가세요.`,
      "창이나 대상이 바뀌면 실행되지 않으며 자동 재시도하지 않습니다.",
    ].join("\n");
  }

  async function refreshStatus() {
    if (state.preview || state.busy) return;
    try {
      const payload = await api("/api/control-page/ui-action", {
        cache: "no-store",
      });
      const status = payload.status || {};
      setState(payload.ok ? status.state || "running" : "unavailable");
      renderMessage(
        payload.ok
          ? "Button 찾기로 대상 창을 읽고, 목록에서 실행 대상을 고르세요."
          : "Host UI Action Bridge가 준비되지 않았습니다.",
        !payload.ok
      );
    } catch (error) {
      setState("unavailable");
      renderMessage(error.message || "화면 행동 상태를 읽지 못했습니다.", true);
    }
    syncControls();
  }

  async function executeDiscoveryRequest() {
    try {
      const payload = await api("/api/control-page/ui-action/targets", {
        method: "POST",
        body: JSON.stringify({}),
      });
      const discovery = payload.targets || null;
      const policy = discovery && discovery.policy;
      if (
        !discovery ||
        discovery.schema !== "ui_action.targets.v1" ||
        !Array.isArray(discovery.targets) ||
        !policy ||
        policy.requiresPreview !== true ||
        policy.requiresExplicitConfirmation !== true ||
        policy.automaticRetry !== false
      ) {
        throw new Error("Button 발견 계약을 확인할 수 없습니다.");
      }
      state.discovery = discovery;
      renderDiscovery();
      setState("targets_observed");
      renderMessage(
        discovery.targets.length
          ? "대상 Button을 고른 뒤 별도 미리보기를 진행하세요."
          : "이 전경 창에는 실행 가능한 이름 있는 Button이 없습니다.",
        false
      );
    } catch (error) {
      clearDiscovery();
      setState("denied");
      renderMessage(error.message || "Button 목록을 읽지 못했습니다.", true);
    } finally {
      state.busy = false;
      syncControls();
    }
  }

  async function executePreviewRequest(requestPayload) {
    try {
      const payload = await api("/api/control-page/ui-action/preview", {
        method: "POST",
        body: JSON.stringify({
          elementId: requestPayload.elementId,
          action: "invoke",
          postcondition: requestPayload.postcondition,
        }),
      });
      state.preview = payload.preview || null;
      if (!state.preview || state.preview.requiresExplicitConfirmation !== true) {
        throw new Error("미리보기 계약을 확인할 수 없습니다.");
      }
      setState("confirmation_required");
      renderPreview();
      state.expiryTimer = window.setInterval(function () {
        if (state.handoff) return;
        if (remainingSeconds() <= 0) {
          clearPreview();
          setState("authorization_required");
          renderMessage("승인 토큰이 만료됐습니다. 다시 미리보기 하세요.", true);
          return;
        }
        renderPreview();
      }, 1000);
    } catch (error) {
      clearPreview();
      setState("denied");
      renderMessage(error.message || "미리보기가 거부됐습니다.", true);
    } finally {
      state.busy = false;
      if (state.preview) renderPreview();
      syncControls();
    }
  }

  async function executeApplyRequest(preview) {
    state.busy = true;
    syncControls();
    if (state.expiryTimer) {
      window.clearInterval(state.expiryTimer);
      state.expiryTimer = null;
    }
    setState("executing");
    renderMessage(
      "대상 창과 요소를 다시 확인하고 승인된 행동 1회를 요청하는 중입니다.",
      false
    );
    try {
      const payload = await api("/api/control-page/ui-action/apply", {
        method: "POST",
        body: JSON.stringify({
          confirmToken: preview.confirmToken,
          userConfirmed: true,
        }),
      });
      clearPreview();
      const result = payload.result || {};
      if (
        result.state !== "verified" ||
        result.executed !== true ||
        result.verified !== true
      ) {
        throw new Error("실행 결과를 검증하지 못했습니다.");
      }
      setState("verified");
      renderMessage("행동 1회를 실행했고 성공 조건까지 확인했습니다.", false);
    } catch (error) {
      clearPreview();
      const result = error.payload && error.payload.result;
      setState(
        result && result.executed ? "outcome_unverified" : "denied"
      );
      renderMessage(
        error.message || "화면 행동이 거부되거나 검증되지 않았습니다.",
        true
      );
    } finally {
      clearDiscovery();
      state.busy = false;
      syncControls();
    }
  }

  function runArmedHandoff(handoff) {
    if (state.handoff !== handoff) return;
    if (state.handoffTimer) {
      window.clearInterval(state.handoffTimer);
      state.handoffTimer = null;
    }
    state.handoff = null;
    if (Date.now() - handoff.deadlineAt > FOCUS_HANDOFF_MAX_LATE_MS) {
      state.busy = false;
      syncControls();
      if (
        handoff.kind === "apply" &&
        state.preview &&
        remainingSeconds() >
          FOCUS_HANDOFF_DELAY_SEC + FOCUS_HANDOFF_TOKEN_MARGIN_SEC
      ) {
        setState("confirmation_required");
        renderPreview();
      } else {
        clearPreview();
        setState("authorization_required");
        renderMessage(
          "브라우저가 지연되어 전경 전환 시한을 넘겼습니다. 요청하지 않았습니다.",
          true
        );
      }
      return;
    }
    if (handoff.kind === "discover") {
      void executeDiscoveryRequest();
    } else if (handoff.kind === "preview") {
      void executePreviewRequest(handoff.payload);
    } else if (handoff.kind === "apply") {
      void executeApplyRequest(handoff.payload.preview);
    }
  }

  function armFocusHandoff(kind, payload) {
    clearHandoff();
    const handoff = {
      kind,
      payload,
      remaining: FOCUS_HANDOFF_DELAY_SEC,
      deadlineAt: Date.now() + FOCUS_HANDOFF_DELAY_SEC * 1000,
    };
    state.handoff = handoff;
    state.busy = true;
    syncControls();
    setState("focus_handoff");
    renderHandoff();
    state.handoffTimer = window.setInterval(function () {
      if (state.handoff !== handoff) return;
      handoff.remaining = Math.max(
        0,
        Math.ceil((handoff.deadlineAt - Date.now()) / 1000)
      );
      if (handoff.remaining <= 0) {
        runArmedHandoff(handoff);
        return;
      }
      renderHandoff();
    }, 1000);
  }

  function cancelFocusHandoff() {
    const kind = state.handoff && state.handoff.kind;
    clearHandoff();
    state.busy = false;
    syncControls();
    if (
      kind === "apply" &&
      state.preview &&
      remainingSeconds() >
        FOCUS_HANDOFF_DELAY_SEC + FOCUS_HANDOFF_TOKEN_MARGIN_SEC
    ) {
      setState("confirmation_required");
      renderPreview();
      return;
    }
    clearPreview();
    if (kind === "discover") clearDiscovery();
    setState("authorization_required");
    renderMessage(
      "전경 전환 카운트다운을 취소했습니다. 아무 요청도 보내지 않았습니다.",
      false
    );
  }

  function discoverTargets() {
    if (state.busy || state.preview) return;
    clearDiscovery();
    armFocusHandoff("discover", {});
  }

  function previewAction(event) {
    event.preventDefault();
    if (state.busy) return;
    const elementId = selectedElementId();
    if (!elementId) {
      renderMessage("먼저 발견된 Button 목록에서 대상을 고르세요.", true);
      return;
    }
    clearPreview();
    armFocusHandoff("preview", {
      elementId,
      postcondition: String(postconditionInput.value || ""),
    });
  }

  function applyAction() {
    if (state.busy || !state.preview) return;
    const preview = state.preview;
    if (
      remainingSeconds() <=
      FOCUS_HANDOFF_DELAY_SEC + FOCUS_HANDOFF_TOKEN_MARGIN_SEC
    ) {
      clearPreview();
      setState("authorization_required");
      renderMessage(
        "전경 전환 전에 승인 토큰이 만료될 수 있습니다. 다시 미리보기 하세요.",
        true
      );
      return;
    }
    if (!window.confirm(confirmationText(preview))) return;
    armFocusHandoff("apply", { preview });
  }

  discoverButton.addEventListener("click", discoverTargets);
  elementInput.addEventListener("change", syncControls);
  form.addEventListener("submit", previewAction);
  mount.addEventListener("click", function (event) {
    const applyButton = event.target.closest("[data-ui-action-apply]");
    const cancelButton = event.target.closest("[data-ui-action-cancel]");
    const handoffCancelButton = event.target.closest(
      "[data-ui-action-handoff-cancel]"
    );
    if (handoffCancelButton) {
      cancelFocusHandoff();
    } else if (applyButton) {
      applyAction();
    } else if (cancelButton && !state.busy) {
      clearPreview();
      setState("authorization_required");
      renderMessage("미리보기를 취소했습니다. 아무 행동도 실행하지 않았습니다.", false);
    }
  });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) refreshStatus();
  });
  clearDiscovery();
  refreshStatus();
  window.setInterval(refreshStatus, 30 * 1000);
})();
