(function () {
  "use strict";

  const mount = document.getElementById("taskApprovalMount");
  const statePill = document.getElementById("taskApprovalStatePill");
  if (!mount || !statePill) return;

  const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
  const SHA256_PATTERN = /^[a-f0-9]{64}$/;
  const TOKEN_PATTERN = /^[A-Za-z0-9_-]{32,256}$/;
  const APPROVAL_PATHS = new Set([
    "/api/control-page/task-approval/preview",
    "/api/control-page/task-approval/apply",
    "/api/control-page/task-approval/cancel",
  ]);
  const DIRTY_STATES = new Set([
    "modified",
    "staged",
    "modified_and_staged",
    "untracked",
    "deleted",
  ]);
  let publicApproval = null;
  let preview = null;
  let confirmToken = "";
  let approvalApiBase = "";
  let approvalCsrfToken = "";
  let busy = false;
  let expiryTimer = null;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function setState(value) {
    const normalized = String(value || "idle");
    statePill.textContent = normalized;
    statePill.dataset.state = normalized;
  }

  function approvalApiCandidates() {
    const candidates = [];
    if (location.protocol === "http:" || location.protocol === "https:") {
      candidates.push(location.origin);
    }
    if (
      location.protocol !== "https:" &&
      location.origin !== "http://127.0.0.1:8799"
    ) {
      candidates.push("http://127.0.0.1:8799");
    }
    return Array.from(new Set(candidates));
  }

  async function ensureApprovalSession() {
    if (approvalApiBase && approvalCsrfToken) return;
    for (const candidate of approvalApiCandidates()) {
      try {
        const response = await fetch(candidate + "/api/control-page/session", {
          cache: "no-store",
        });
        if (!response.ok) continue;
        const payload = await response.json();
        const token = String(payload.csrfToken || "");
        if (
          payload.ok !== true ||
          payload.csrfHeader !== "X-Evelyn-CSRF-Token" ||
          !TOKEN_PATTERN.test(token)
        ) continue;
        approvalApiBase = candidate;
        approvalCsrfToken = token;
        return;
      } catch {
        // Try the next fixed Control Page candidate.
      }
    }
    throw new Error("Control Page 승인 API에 연결하지 못했습니다.");
  }

  async function api(path, options) {
    if (
      !APPROVAL_PATHS.has(path) ||
      String(options && options.method || "").toUpperCase() !== "POST"
    ) {
      throw new Error("승인 API 요청이 허용 범위를 벗어났습니다.");
    }
    await ensureApprovalSession();
    const request = {
      cache: "no-store",
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Evelyn-CSRF-Token": approvalCsrfToken,
      },
      body: options.body === undefined ? "{}" : options.body,
    };
    // This mutation request is deliberately sent once. A 403 only clears the
    // CSRF session for the user's next explicit click; it is never replayed.
    const response = await fetch(approvalApiBase + path, request);
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (response.status === 403) approvalCsrfToken = "";
    if (!response.ok || payload.ok === false) {
      const error = new Error(
        payload.message || payload.error || `HTTP ${response.status}`
      );
      throw error;
    }
    return payload;
  }

  function clearApprovalSecret() {
    confirmToken = "";
    preview = null;
    if (expiryTimer) {
      window.clearInterval(expiryTimer);
      expiryTimer = null;
    }
  }

  function sameApproval(left, right) {
    return Boolean(
      left &&
      right &&
      left.taskId === right.taskId &&
      left.approvalId === right.approvalId
    );
  }

  function validPublicApproval(value) {
    if (!value || typeof value !== "object") return null;
    const taskId = String(value.taskId || "");
    const approvalId = String(value.approvalId || "");
    const tool = String(value.tool || "");
    const state = String(value.state || "awaiting_approval");
    const step = Number(value.step);
    const maxSteps = Number(value.maxSteps);
    const expiresAt = Number(value.expiresAt);
    if (value.schema !== "task_approval.public.v1") return null;
    if (!ID_PATTERN.test(taskId) || !ID_PATTERN.test(approvalId)) return null;
    if (!["workspace_edit", "workspace_test"].includes(tool)) return null;
    if (!["awaiting_approval", "claimed", "cancelling", "resuming", "cancelled", "expired", "uncertain"].includes(state)) return null;
    if (!Number.isInteger(step) || !Number.isInteger(maxSteps) || step < 1 || step > maxSteps || maxSteps > 10) return null;
    if (!Number.isFinite(expiresAt) || expiresAt <= 0 || expiresAt > 8.64e12) return null;
    return {
      schema: String(value.schema || ""),
      state,
      taskId,
      approvalId,
      step,
      maxSteps,
      tool,
      effect: String(value.effect || ""),
      expiresAt,
    };
  }

  function remainingSeconds() {
    return Math.max(
      0,
      Math.ceil(Number(preview && preview.confirmExpiresAt) - Date.now() / 1000)
    );
  }

  function renderMessage(message, isError) {
    mount.replaceChildren(
      element(
        "span",
        isError ? "task-approval-error" : "task-approval-meta",
        message
      ),
      element(
        "span",
        "task-approval-policy",
        "일반 채팅·음성·모델 응답은 승인으로 취급하지 않습니다. 자동 재시도도 하지 않습니다."
      )
    );
  }

  function pendingCard() {
    const card = element("article", "task-approval-card");
    const head = element("div", "task-approval-preview-head");
    head.append(
      element("strong", "", "사용자 승인 대기"),
      element(
        "span",
        "task-approval-pill",
        `${publicApproval.step}/${publicApproval.maxSteps}`
      )
    );
    const details = element("dl", "task-approval-binding");
    addBinding(details, "작업", publicApproval.taskId);
    addBinding(details, "단계", `${publicApproval.step}/${publicApproval.maxSteps}`);
    addBinding(details, "도구", publicApproval.tool);
    addBinding(details, "효과", publicApproval.effect || "파일 변경");
    addBinding(details, "승인 대기 만료", new Date(publicApproval.expiresAt * 1000).toLocaleString());
    const actions = element("div", "task-approval-actions");
    if (publicApproval.tool === "workspace_test") {
      const unavailable = element("button", "", "테스트 승인 불가");
      unavailable.type = "button";
      unavailable.disabled = true;
      actions.append(unavailable, cancelButton());
      card.append(
        head,
        details,
        element(
          "span",
          "task-approval-error",
          "호스트 테스트는 코드 실행 권한입니다. 격리 sandbox가 준비될 때까지 승인 토큰을 발급하지 않습니다."
        ),
        actions
      );
      return card;
    }
    const review = element("button", "is-primary", "전체 diff 검토");
    review.type = "button";
    review.dataset.taskApprovalReview = "1";
    review.disabled = busy;
    actions.append(review, cancelButton());
    card.append(
      head,
      details,
      actions,
      element(
        "span",
        "task-approval-policy",
        "검토를 누르면 이 단계에만 묶인 30초짜리 1회 토큰을 발급합니다."
      )
    );
    return card;
  }

  function addBinding(list, label, value) {
    list.append(
      element("dt", "", label),
      element("dd", "", String(value || ""))
    );
  }

  function cancelButton() {
    const cancel = element("button", "", "작업 취소");
    cancel.type = "button";
    cancel.dataset.taskApprovalCancel = "1";
    cancel.disabled = busy;
    return cancel;
  }

  function validatePreview(value, confirmExpiresAt) {
    if (!value || typeof value !== "object") return null;
    const step = Number(value.step);
    const maxSteps = Number(value.maxSteps);
    const baseSha256 = String(value.baseSha256 || "");
    const candidateSha256 = String(value.candidateSha256 || "");
    const dirtyStatus = String(value.dirtyStatus || "");
    const dirtyRequired = value.dirtyBaseAcknowledgementRequired === true;
    if (
      !sameApproval(value, publicApproval) ||
      publicApproval.state !== "awaiting_approval" ||
      !Number.isInteger(step) ||
      !Number.isInteger(maxSteps) ||
      step !== publicApproval.step ||
      maxSteps !== publicApproval.maxSteps ||
      step < 1 ||
      step > maxSteps ||
      value.schema !== "task_approval.preview.v1" ||
      value.tool !== "workspace_edit" ||
      !["create", "replace"].includes(value.mode) ||
      typeof value.path !== "string" ||
      !value.path ||
      !(baseSha256 === "ABSENT" || SHA256_PATTERN.test(baseSha256)) ||
      !SHA256_PATTERN.test(candidateSha256) ||
      !SHA256_PATTERN.test(String(value.diffSha256 || "")) ||
      !SHA256_PATTERN.test(String(value.previewDigest || "")) ||
      typeof value.fullDiff !== "string" ||
      !value.fullDiff ||
      value.diffTruncated !== false ||
      value.requiresExplicitConfirmation !== true ||
      value.automaticRetry !== false ||
      !["clean", "modified", "staged", "modified_and_staged", "untracked", "deleted", "absent"].includes(dirtyStatus) ||
      (DIRTY_STATES.has(dirtyStatus) && !dirtyRequired) ||
      (!DIRTY_STATES.has(dirtyStatus) && dirtyRequired) ||
      !Number.isFinite(confirmExpiresAt) ||
      confirmExpiresAt <= Date.now() / 1000 ||
      confirmExpiresAt > Date.now() / 1000 + 31
    ) {
      return null;
    }
    return {
      schema: value.schema,
      taskId: value.taskId,
      approvalId: value.approvalId,
      step,
      maxSteps,
      tool: value.tool,
      effect: String(value.effect || publicApproval.effect || "파일 변경"),
      path: String(value.path || ""),
      mode: value.mode,
      baseSha256,
      candidateSha256,
      diffSha256: String(value.diffSha256),
      previewDigest: String(value.previewDigest),
      fullDiff: value.fullDiff,
      dirtyStatus,
      gitStatus: String(value.gitStatus || ""),
      tracked: value.tracked === true,
      dirtyBaseAcknowledgementRequired: dirtyRequired,
      confirmExpiresAt,
    };
  }

  function renderPreview() {
    if (!preview) return;
    const card = element("article", "task-approval-card");
    const head = element("div", "task-approval-preview-head");
    head.append(
      element("strong", "", "전체 변경 내용 확인"),
      element("span", "task-approval-pill", `${remainingSeconds()}초`)
    );
    const details = element("dl", "task-approval-binding");
    addBinding(details, "작업 / 단계", `${preview.taskId} · ${preview.step}/${preview.maxSteps}`);
    addBinding(details, "경로", preview.path);
    addBinding(details, "방식", preview.mode);
    addBinding(details, "추적 여부", preview.tracked ? "tracked" : "untracked");
    addBinding(details, "현재 SHA", preview.baseSha256);
    addBinding(details, "후보 SHA", preview.candidateSha256);
    addBinding(details, "Git 상태", `${preview.dirtyStatus}${preview.gitStatus ? ` (${preview.gitStatus})` : ""}`);
    addBinding(details, "Diff SHA", preview.diffSha256);
    addBinding(details, "미리보기 digest", preview.previewDigest);
    addBinding(
      details,
      "1회 토큰 만료",
      new Date(preview.confirmExpiresAt * 1000).toLocaleString()
    );

    const diff = element("pre", "task-approval-diff", preview.fullDiff);
    diff.setAttribute("aria-label", "전체 unified diff");
    diff.tabIndex = 0;
    const sandboxNotice = element(
      "span",
      "task-approval-policy",
      "선택한 sandbox 테스트 영수증은 후보 검토용 관측이며 동작 해결의 증명이 아닙니다. 행동형 작업은 적용 후에도 미검증으로 보고됩니다."
    );

    const exactCheck = element("label", "task-approval-check");
    const exactInput = element("input");
    exactInput.type = "checkbox";
    exactInput.dataset.taskApprovalExactCheck = "1";
    exactCheck.append(
      exactInput,
      element("span", "", "위 전체 diff와 현재/후보 SHA를 확인했으며 이 파일 1개를 한 번 적용하는 데 동의합니다.")
    );

    let dirtyCheck = null;
    if (preview.dirtyBaseAcknowledgementRequired) {
      const dirtyLabel = element("label", "task-approval-check");
      dirtyCheck = element("input");
      dirtyCheck.type = "checkbox";
      dirtyCheck.dataset.taskApprovalDirtyCheck = "1";
      dirtyLabel.append(
        dirtyCheck,
        element("span", "", "이 파일의 기존 modified/staged/untracked 상태가 사용자 소유일 수 있음을 확인했고, 표시된 현재 base 기준 변경을 명시적으로 승인합니다.")
      );
      card.append(head, details, diff, sandboxNotice, exactCheck, dirtyLabel);
    } else {
      card.append(head, details, diff, sandboxNotice, exactCheck);
    }

    const actions = element("div", "task-approval-actions");
    const apply = element("button", "is-primary", "이 변경 1회 승인");
    apply.type = "button";
    apply.dataset.taskApprovalApply = "1";
    apply.disabled = true;
    actions.append(apply, cancelButton());
    card.append(
      actions,
      element(
        "span",
        "task-approval-policy",
        "승인은 정확한 작업·grant·action·단계·도구·인자 hash·base/candidate SHA·diff digest에만 묶입니다. 결과가 불확실하면 재시도하지 않습니다."
      )
    );

    const syncApply = function () {
      apply.disabled = Boolean(
        busy ||
        remainingSeconds() <= 0 ||
        !exactInput.checked ||
        (dirtyCheck && !dirtyCheck.checked)
      );
    };
    exactInput.addEventListener("change", syncApply);
    if (dirtyCheck) dirtyCheck.addEventListener("change", syncApply);
    syncApply();
    mount.replaceChildren(card);
  }

  function render() {
    if (preview) {
      setState("preview_ready");
      renderPreview();
      return;
    }
    if (!publicApproval) {
      setState("idle");
      renderMessage("승인이 필요한 작업 단계가 없습니다.", false);
      return;
    }
    if (["claimed", "applying", "cancelling", "resuming"].includes(publicApproval.state)) {
      setState("resuming");
      renderMessage("승인된 변경 1회의 결과를 확인하고 작업 루프를 이어가는 중입니다.", false);
      return;
    }
    setState("awaiting_approval");
    mount.replaceChildren(pendingCard());
  }

  async function requestPreview() {
    if (busy || !publicApproval || publicApproval.tool !== "workspace_edit") return;
    busy = true;
    render();
    try {
      const payload = await api("/api/control-page/task-approval/preview", {
        method: "POST",
        body: JSON.stringify({
          taskId: publicApproval.taskId,
          approvalId: publicApproval.approvalId,
        }),
      });
      const issuedToken = String(payload.confirmToken || "");
      const safePreview = validatePreview(
        payload.preview,
        Number(payload.confirmExpiresAt)
      );
      if (!safePreview || !TOKEN_PATTERN.test(issuedToken)) {
        throw new Error("전체 diff 승인 계약을 확인할 수 없습니다.");
      }
      confirmToken = issuedToken;
      preview = safePreview;
      expiryTimer = window.setInterval(function () {
        if (!preview) return;
        if (remainingSeconds() <= 0) {
          clearApprovalSecret();
          setState("denied");
          renderMessage("1회 승인 토큰이 만료됐습니다. 전체 diff를 다시 검토하세요.", true);
          return;
        }
        const pill = mount.querySelector(".task-approval-preview-head .task-approval-pill");
        if (pill) pill.textContent = `${remainingSeconds()}초`;
      }, 1000);
    } catch (error) {
      clearApprovalSecret();
      setState("denied");
      renderMessage(error.message || "변경 미리보기를 열지 못했습니다.", true);
    } finally {
      busy = false;
      if (preview) render();
    }
  }

  async function applyApproval() {
    if (busy || !publicApproval || !preview || !confirmToken) return;
    const exactCheck = mount.querySelector("[data-task-approval-exact-check]");
    const dirtyCheck = mount.querySelector("[data-task-approval-dirty-check]");
    const dirtyAcknowledged = dirtyCheck ? dirtyCheck.checked === true : false;
    if (
      !exactCheck ||
      exactCheck.checked !== true ||
      (preview.dirtyBaseAcknowledgementRequired && !dirtyAcknowledged)
    ) return;
    busy = true;
    const tokenForOneRequest = confirmToken;
    const taskId = preview.taskId;
    const approvalId = preview.approvalId;
    clearApprovalSecret();
    setState("resuming");
    renderMessage("승인된 변경 1회를 적용하고 SHA를 다시 확인하는 중입니다.", false);
    try {
      await api("/api/control-page/task-approval/apply", {
        method: "POST",
        body: JSON.stringify({
          taskId,
          approvalId,
          confirmToken: tokenForOneRequest,
          userConfirmed: true,
          dirtyBaseAcknowledged: dirtyAcknowledged,
        }),
      });
      setState("resuming");
      renderMessage("적용 요청을 1회 접수했습니다. 최종 결과는 작업 상태에서 확인합니다.", false);
    } catch (error) {
      setState("uncertain");
      renderMessage(
        error.message || "적용 결과를 확인하지 못했습니다. 안전을 위해 자동 재시도하지 않습니다.",
        true
      );
    } finally {
      busy = false;
    }
  }

  async function cancelApproval() {
    if (busy || !publicApproval) return;
    busy = true;
    const taskId = publicApproval.taskId;
    const approvalId = publicApproval.approvalId;
    clearApprovalSecret();
    setState("cancelled");
    renderMessage("정확한 승인 대기와 작업 취소를 요청하는 중입니다.", false);
    try {
      await api("/api/control-page/task-approval/cancel", {
        method: "POST",
        body: JSON.stringify({ taskId, approvalId }),
      });
      publicApproval = null;
      setState("cancelled");
      renderMessage("승인 대기와 작업을 취소했습니다. 어떤 변경도 자동 재시도하지 않습니다.", false);
    } catch (error) {
      setState("uncertain");
      renderMessage(error.message || "취소 결과를 확인하지 못했습니다.", true);
    } finally {
      busy = false;
    }
  }

  function receivePublicState(value) {
    const next = validPublicApproval(value);
    const keepPreview = Boolean(
      preview &&
      sameApproval(publicApproval, next) &&
      next &&
      next.state === "awaiting_approval"
    );
    if (!keepPreview) clearApprovalSecret();
    publicApproval = next;
    if (!busy && !keepPreview) render();
  }

  mount.addEventListener("click", function (event) {
    if (event.target.closest("[data-task-approval-review]")) {
      void requestPreview();
    } else if (event.target.closest("[data-task-approval-apply]")) {
      void applyApproval();
    } else if (event.target.closest("[data-task-approval-cancel]")) {
      void cancelApproval();
    }
  });
  window.addEventListener("evelyn:task-approval-state", function (event) {
    receivePublicState(event.detail && event.detail.approval);
  });
  receivePublicState(window.EvelynTaskApprovalPublicState || null);
})();
