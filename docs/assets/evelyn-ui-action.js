(function () {
  "use strict";

  const mount = document.getElementById("uiActionMount");
  const form = document.getElementById("uiActionPreviewForm");
  const elementInput = document.getElementById("uiActionElementId");
  const postconditionInput = document.getElementById("uiActionPostcondition");
  const previewButton = document.getElementById("uiActionPreviewButton");
  const statePill = document.getElementById("uiActionStatePill");
  if (
    !mount ||
    !form ||
    !elementInput ||
    !postconditionInput ||
    !previewButton ||
    !statePill
  ) {
    return;
  }

  const state = {
    busy: false,
    preview: null,
    expiryTimer: null,
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
    const applyButton = element("button", "is-primary", "확인 후 1회 실행");
    applyButton.type = "button";
    applyButton.dataset.uiActionApply = "1";
    applyButton.disabled = state.busy || remainingSeconds() <= 0;
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
        "실행 직전 같은 창·같은 요소를 재관찰하고, 실행 뒤 성공 조건까지 확인합니다."
      )
    );
    mount.replaceChildren(card);
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
          ? "현재 전경 창에서 관찰된 Button의 elementId를 입력하세요."
          : "Host UI Action Bridge가 준비되지 않았습니다.",
        !payload.ok
      );
    } catch (error) {
      setState("unavailable");
      renderMessage(error.message || "화면 행동 상태를 읽지 못했습니다.", true);
    }
  }

  async function previewAction(event) {
    event.preventDefault();
    if (state.busy) return;
    const elementId = String(elementInput.value || "").trim().toLowerCase();
    if (!/^[0-9a-f]{20}$/.test(elementId)) {
      renderMessage("elementId는 소문자 16진수 20자리여야 합니다.", true);
      return;
    }
    clearPreview();
    state.busy = true;
    previewButton.disabled = true;
    try {
      const payload = await api("/api/control-page/ui-action/preview", {
        method: "POST",
        body: JSON.stringify({
          elementId,
          action: "invoke",
          postcondition: String(postconditionInput.value || ""),
        }),
      });
      state.preview = payload.preview || null;
      if (!state.preview || state.preview.requiresExplicitConfirmation !== true) {
        throw new Error("미리보기 계약을 확인할 수 없습니다.");
      }
      setState("confirmation_required");
      renderPreview();
      state.expiryTimer = window.setInterval(function () {
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
      previewButton.disabled = false;
      if (state.preview) renderPreview();
    }
  }

  async function applyAction() {
    if (state.busy || !state.preview) return;
    const preview = state.preview;
    if (remainingSeconds() <= 0) {
      clearPreview();
      setState("authorization_required");
      renderMessage("승인 토큰이 만료됐습니다. 다시 미리보기 하세요.", true);
      return;
    }
    if (!window.confirm(confirmationText(preview))) return;
    state.busy = true;
    renderPreview();
    setState("executing");
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
      state.busy = false;
      previewButton.disabled = false;
    }
  }

  form.addEventListener("submit", previewAction);
  mount.addEventListener("click", function (event) {
    const applyButton = event.target.closest("[data-ui-action-apply]");
    const cancelButton = event.target.closest("[data-ui-action-cancel]");
    if (applyButton) {
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
  refreshStatus();
  window.setInterval(refreshStatus, 30 * 1000);
})();
