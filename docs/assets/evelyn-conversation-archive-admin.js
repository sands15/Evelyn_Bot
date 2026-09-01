(() => {
  "use strict";

  const apiPrefix = "/api/control-page/conversation-archive/admin";
  const panel = document.getElementById("conversationArchiveAdminPanel");
  const archiveAdminPage = location.pathname === "/archive/admin";
  let bootstrapNonce = "";
  if (
    location.hash &&
    (archiveAdminPage || location.hash.startsWith("#archive-bootstrap="))
  ) {
    const match = archiveAdminPage
      ? /^#archive-bootstrap=([A-Za-z0-9_-]{22,128})$/.exec(location.hash)
      : null;
    if (match) bootstrapNonce = match[1];
    try {
      history.replaceState(history.state, "", location.pathname + location.search);
    } catch {
      bootstrapNonce = "";
      location.replace(location.pathname + location.search);
      return;
    }
  }
  if (!panel || location.pathname !== "/archive/admin") return;

  const status = document.getElementById("conversationArchiveStatus");
  const login = document.getElementById("conversationArchiveLogin");
  const challengeButton = document.getElementById("conversationArchiveChallengeButton");
  const loginForm = document.getElementById("conversationArchiveLoginForm");
  const loginCode = document.getElementById("conversationArchiveLoginCode");
  const logoutButton = document.getElementById("conversationArchiveLogoutButton");
  const recordsPanel = document.getElementById("conversationArchiveRecordsPanel");
  const recordsMount = document.getElementById("conversationArchiveRecords");
  const refreshButton = document.getElementById("conversationArchiveRefreshButton");
  const nextButton = document.getElementById("conversationArchiveNextButton");
  const previewButton = document.getElementById("conversationArchivePreviewDeleteButton");
  const deleteStatus = document.getElementById("conversationArchiveDeleteStatus");
  const applyForm = document.getElementById("conversationArchiveApplyDeleteForm");
  const deleteCode = document.getElementById("conversationArchiveDeleteCode");
  const participationMount = document.getElementById("conversationArchiveParticipation");
  const participationRefreshButton = document.getElementById("conversationArchiveParticipationRefreshButton");
  const participationNextButton = document.getElementById("conversationArchiveParticipationNextButton");
  const voiceTransitionsMount = document.getElementById("conversationArchiveVoiceTransitions");
  const voiceTransitionsRefreshButton = document.getElementById("conversationArchiveVoiceTransitionsRefreshButton");
  const voiceTransitionsNextButton = document.getElementById("conversationArchiveVoiceTransitionsNextButton");
  const legalMount = document.getElementById("conversationArchiveLegalEvents");
  const legalRefreshButton = document.getElementById("conversationArchiveLegalRefreshButton");
  const legalNextButton = document.getElementById("conversationArchiveLegalNextButton");
  const feedbackMount = document.getElementById("conversationArchiveFeedbackWorkflows");
  const feedbackStatus = document.getElementById("conversationArchiveFeedbackStatus");
  const feedbackRefreshButton = document.getElementById("conversationArchiveFeedbackRefreshButton");
  const feedbackCaptureForm = document.getElementById("conversationArchiveFeedbackCaptureForm");
  const feedbackGeneralizeForm = document.getElementById("conversationArchiveFeedbackGeneralizeForm");
  const feedbackEvaluateForm = document.getElementById("conversationArchiveFeedbackEvaluateForm");
  const feedbackApprovalPreviewForm = document.getElementById("conversationArchiveFeedbackApprovalPreviewForm");
  const feedbackApprovalApplyForm = document.getElementById("conversationArchiveFeedbackApprovalApplyForm");
  const feedbackApprovalGuidance = document.getElementById("conversationArchiveFeedbackApprovalGuidance");
  const feedbackCanaryForm = document.getElementById("conversationArchiveFeedbackCanaryForm");
  const feedbackActivateForm = document.getElementById("conversationArchiveFeedbackActivateForm");
  const feedbackFailureForm = document.getElementById("conversationArchiveFeedbackFailureForm");
  const feedbackRollbackPreviewForm = document.getElementById("conversationArchiveFeedbackRollbackPreviewForm");
  const feedbackRollbackApplyForm = document.getElementById("conversationArchiveFeedbackRollbackApplyForm");
  const feedbackRevokePreviewForm = document.getElementById("conversationArchiveFeedbackRevokePreviewForm");
  const feedbackRevokeApplyForm = document.getElementById("conversationArchiveFeedbackRevokeApplyForm");

  let csrfToken = "";
  let challengeId = "";
  let previewToken = "";
  let nextCursor = "";
  let feedbackApprovalPreviewToken = "";
  let feedbackRollbackPreviewToken = "";
  let feedbackRevokePreviewToken = "";
  let busy = false;
  let recordsLoading = false;
  const metadataPages = {
    participation: {
      mount: participationMount,
      refreshButton: participationRefreshButton,
      nextButton: participationNextButton,
      responseField: "intervals",
      cursor: "",
      loading: false,
      render: renderParticipation
    },
    "voice-state-transitions": {
      mount: voiceTransitionsMount,
      refreshButton: voiceTransitionsRefreshButton,
      nextButton: voiceTransitionsNextButton,
      responseField: "transitions",
      cursor: "",
      loading: false,
      render: renderVoiceStateTransitions
    },
    "legal-minimal": {
      mount: legalMount,
      refreshButton: legalRefreshButton,
      nextButton: legalNextButton,
      responseField: "events",
      cursor: "",
      loading: false,
      render: renderLegalEvents
    }
  };

  function setAuthenticated(authenticated) {
    login.hidden = authenticated;
    logoutButton.hidden = !authenticated;
    recordsPanel.hidden = !authenticated;
    if (!authenticated) {
      challengeId = "";
      loginForm.hidden = true;
      loginCode.value = "";
      recordsMount.replaceChildren();
      previewToken = "";
      nextCursor = "";
      nextButton.hidden = true;
      applyForm.hidden = true;
      deleteCode.value = "";
      deleteStatus.textContent = "";
      feedbackApprovalPreviewToken = "";
      feedbackRollbackPreviewToken = "";
      feedbackRevokePreviewToken = "";
      feedbackApprovalApplyForm.hidden = true;
      feedbackRollbackApplyForm.hidden = true;
      feedbackRevokeApplyForm.hidden = true;
      feedbackApprovalGuidance.hidden = true;
      feedbackApprovalGuidance.textContent = "";
      feedbackMount.replaceChildren();
      feedbackStatus.textContent = "";
      for (const page of Object.values(metadataPages)) {
        page.mount.replaceChildren();
        page.cursor = "";
        page.nextButton.hidden = true;
      }
      updatePreviewButton();
    }
  }

  async function ensureCsrfToken() {
    if (csrfToken) return csrfToken;
    const response = await fetch("/api/control-page/session", {
      cache: "no-store",
      credentials: "same-origin"
    });
    if (!response.ok) throw new Error("csrf_unavailable");
    const payload = await response.json();
    csrfToken = String(payload.csrfToken || "");
    if (!csrfToken) throw new Error("csrf_unavailable");
    return csrfToken;
  }

  async function archiveRequest(path, options = {}) {
    if (location.protocol !== "https:") {
      return { status: 503, payload: { ok: false } };
    }
    try {
      const method = String(options.method || "GET").toUpperCase();
      const headers = {};
      const request = {
        method,
        headers,
        cache: "no-store",
        credentials: "same-origin",
        referrerPolicy: "same-origin"
      };
      if (method === "POST") {
        const encodedBody = JSON.stringify(options.body || {});
        headers["Content-Type"] = "application/json";
        headers["X-Evelyn-CSRF-Token"] = await ensureCsrfToken();
        request.body = encodedBody;
      }
      const response = await fetch(apiPrefix + path, request);
      let payload = {};
      try {
        const candidate = await response.json();
        if (candidate && typeof candidate === "object" && !Array.isArray(candidate)) {
          payload = candidate;
        }
      } catch {
        // Failures stay content-free in the UI.
      }
      return { status: response.status, payload };
    } catch {
      return { status: 503, payload: { ok: false } };
    }
  }

  function renderRecords(records) {
    recordsMount.replaceChildren();
    previewToken = "";
    applyForm.hidden = true;
    deleteCode.value = "";
    deleteStatus.textContent = "";
    const rows = Array.isArray(records) ? records : [];
    for (const record of rows) {
      if (!record || typeof record !== "object") continue;
      const recordId = String(record.recordId || "");
      if (!recordId) continue;
      const row = document.createElement("label");
      row.className = "conversation-archive-admin-record";

      const head = document.createElement("span");
      head.className = "conversation-archive-admin-record-head";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = recordId;
      checkbox.addEventListener("change", updatePreviewButton);
      const kind = document.createElement("strong");
      kind.textContent = String(record.kind || "record");
      head.append(checkbox, kind);

      const meta = document.createElement("span");
      meta.className = "conversation-archive-admin-meta";
      meta.textContent = [record.createdAt, record.ownerName, recordId]
        .filter((value) => value !== undefined && value !== null && String(value))
        .map(String)
        .join(" · ");

      const body = document.createElement("pre");
      body.textContent = String(record.body ?? "");
      row.append(head, meta, body);
      recordsMount.append(row);
    }
    if (!recordsMount.childElementCount) {
      const empty = document.createElement("span");
      empty.className = "conversation-archive-admin-status";
      empty.textContent = "표시할 기록이 없습니다.";
      recordsMount.append(empty);
    }
    updatePreviewButton();
  }

  function appendMetadataRow(mount, title, values) {
    const row = document.createElement("div");
    row.className = "conversation-archive-admin-record";
    const head = document.createElement("strong");
    head.textContent = String(title);
    const meta = document.createElement("span");
    meta.className = "conversation-archive-admin-meta";
    meta.textContent = values
      .filter((value) => value !== undefined && value !== null && String(value))
      .map(String)
      .join(" · ");
    row.append(head, meta);
    mount.append(row);
  }

  function appendEmptyRow(mount, text) {
    if (mount.childElementCount) return;
    const empty = document.createElement("span");
    empty.className = "conversation-archive-admin-status";
    empty.textContent = text;
    mount.append(empty);
  }

  function renderParticipation(intervals) {
    participationMount.replaceChildren();
    for (const interval of Array.isArray(intervals) ? intervals : []) {
      if (!interval || typeof interval !== "object") continue;
      appendMetadataRow(
        participationMount,
        interval.kind === "eligible" ? "기록 가능 구간" : "음성 참여 구간",
        [
          interval.startedAt,
          interval.endedAt || "진행 중",
          interval.ownerName,
          `서버 ${String(interval.guildId || "")}`,
          `채널 ${String(interval.channelId || "")}`,
          `사용자 ${String(interval.principalId || "")}`,
          `구간 ${String(interval.intervalId || "")}`
        ]
      );
    }
    appendEmptyRow(participationMount, "표시할 음성 참여 구간이 없습니다.");
  }

  function renderVoiceStateTransitions(transitions) {
    voiceTransitionsMount.replaceChildren();
    for (const transition of Array.isArray(transitions) ? transitions : []) {
      if (!transition || typeof transition !== "object") continue;
      appendMetadataRow(
        voiceTransitionsMount,
        "음성 상태 전이",
        [
          transition.eventAt,
          transition.ownerName,
          `서버 ${String(transition.guildId || "")}`,
          `채널 ${String(transition.channelId || "")}`,
          `참여 ${transition.present === true ? "예" : "아니요"}`,
          `동의 ${transition.consentCurrent === true ? "예" : "아니요"}`,
          `본인 음소거 ${transition.selfMute === true ? "예" : "아니요"}`,
          `서버 음소거 ${transition.serverMute === true ? "예" : "아니요"}`,
          `본인 듣기 차단 ${transition.selfDeaf === true ? "예" : "아니요"}`,
          `서버 듣기 차단 ${transition.serverDeaf === true ? "예" : "아니요"}`,
          `발언 억제 ${transition.suppressed === true ? "예" : "아니요"}`,
          `게이트웨이 확인 ${transition.gatewayKnown === true ? "예" : "아니요"}`,
          `사용자 ${String(transition.principalId || "")}`,
          `전이 ${String(transition.transitionId || "")}`
        ]
      );
    }
    appendEmptyRow(voiceTransitionsMount, "표시할 음성 상태 전이가 없습니다.");
  }

  function renderLegalEvents(events) {
    legalMount.replaceChildren();
    for (const event of Array.isArray(events) ? events : []) {
      if (!event || typeof event !== "object") continue;
      appendMetadataRow(
        legalMount,
        "법적 최소 보존",
        [event.ownerName, event.occurredAt]
      );
    }
    appendEmptyRow(legalMount, "표시할 법적 최소 보존 항목이 없습니다.");
  }

  function renderFeedbackWorkflows(workflows, activeVersionId) {
    feedbackMount.replaceChildren();
    for (const workflow of Array.isArray(workflows) ? workflows : []) {
      if (!workflow || typeof workflow !== "object" || !workflow.workflowId) continue;
      appendMetadataRow(
        feedbackMount,
        String(workflow.state || "routed"),
        [
          `워크플로 ${String(workflow.workflowId)}`,
          workflow.versionId ? `버전 ${String(workflow.versionId)}` : "버전 없음",
          `분류 ${String(workflow.category || "삭제됨")}`,
          `경로 ${String(workflow.route || "unknown")}`,
          workflow.actionable === true ? "개선 가능" : "검토 전용",
          Array.isArray(workflow.deletionStates) && workflow.deletionStates.length
            ? `삭제 ${workflow.deletionStates.map(String).join(" → ")}`
            : ""
        ]
      );
    }
    appendEmptyRow(feedbackMount, "표시할 피드백 워크플로가 없습니다.");
    feedbackStatus.textContent = `현재 활성 규칙 버전: ${String(activeVersionId || "base")}`;
  }

  function feedbackFormValue(form, name) {
    const field = form?.elements?.namedItem(name);
    return field && typeof field.value === "string" ? field.value : "";
  }

  function feedbackNonce() {
    const values = new Uint8Array(16);
    crypto.getRandomValues(values);
    return Array.from(values, (value) => value.toString(16).padStart(2, "0")).join("");
  }

  function parseFeedbackJson(value) {
    const parsed = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("feedback_json_invalid");
    }
    return parsed;
  }

  async function loadFeedbackWorkflows() {
    const result = await archiveRequest("/feedback/workflows", {
      method: "POST",
      body: {}
    });
    if (handleUnavailable(result) || handleAuthenticationRequired(result)) return;
    if (result.payload.ok !== true) {
      feedbackStatus.textContent = "피드백 상태를 불러오지 못했습니다.";
      return;
    }
    renderFeedbackWorkflows(
      result.payload.workflows,
      result.payload.activeVersionId
    );
  }

  async function submitFeedback(path, body, successText) {
    if (busy) return null;
    busy = true;
    feedbackStatus.textContent = "검증된 저장 경로에 반영하는 중입니다.";
    try {
      const result = await archiveRequest(path, { method: "POST", body });
      if (handleUnavailable(result) || handleAuthenticationRequired(result)) return null;
      if (result.payload.ok !== true) {
        feedbackStatus.textContent = "요청이 게이트를 통과하지 못했습니다.";
        return null;
      }
      feedbackStatus.textContent = successText;
      return result.payload;
    } finally {
      busy = false;
    }
  }

  function selectedRecordIds() {
    return Array.from(recordsMount.querySelectorAll('input[type="checkbox"]:checked'))
      .map((input) => input.value)
      .filter(Boolean);
  }

  function updatePreviewButton() {
    const stepUpPending = Boolean(previewToken);
    const metadataLoading = Object.values(metadataPages).some((page) => page.loading);
    previewButton.disabled = busy || stepUpPending || selectedRecordIds().length === 0;
    refreshButton.disabled = recordsLoading || stepUpPending;
    nextButton.disabled = recordsLoading || stepUpPending;
    for (const page of Object.values(metadataPages)) {
      page.refreshButton.disabled = page.loading || stepUpPending;
      page.nextButton.disabled = page.loading || stepUpPending;
    }
    challengeButton.disabled = busy || recordsLoading || metadataLoading || !bootstrapNonce;
  }

  function handleUnavailable(result) {
    if (result.status < 500) return false;
    bootstrapNonce = "";
    challengeButton.disabled = true;
    setAuthenticated(false);
    panel.hidden = true;
    return true;
  }

  function handleAuthenticationRequired(result) {
    if (
      result.status !== 401 &&
      result.payload.state !== "authentication_required"
    ) {
      return false;
    }
    panel.hidden = false;
    setAuthenticated(false);
    status.textContent = bootstrapNonce
      ? "OTP 보내기를 눌러 관리자 확인을 계속하세요."
      : "Windows 관리자 런처를 다시 실행해 이 페이지를 여세요.";
    return true;
  }

  async function loadRecords(cursor = "") {
    if (recordsLoading) return;
    recordsLoading = true;
    updatePreviewButton();
    try {
      const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
      const result = await archiveRequest("/records" + query);
      if (handleUnavailable(result)) return;
      panel.hidden = false;
      if (handleAuthenticationRequired(result)) return;
      if (result.payload.ok !== true) {
        setAuthenticated(true);
        recordsMount.replaceChildren();
        nextCursor = "";
        nextButton.hidden = true;
        status.textContent = "기록 페이지를 불러오지 못했습니다. 첫 페이지부터 다시 확인하세요.";
        return;
      }
      setAuthenticated(true);
      bootstrapNonce = "";
      challengeButton.disabled = true;
      status.textContent = "관리자 세션이 확인되었습니다. 기록은 현재 한 페이지만 표시됩니다.";
      renderRecords(result.payload.records);
      nextCursor = typeof result.payload.nextCursor === "string"
        ? result.payload.nextCursor
        : "";
      nextButton.hidden = !nextCursor;
    } finally {
      recordsLoading = false;
      updatePreviewButton();
    }
  }

  async function loadMetadataPage(kind, cursor = "") {
    const page = metadataPages[kind];
    if (!page || page.loading) return;
    page.loading = true;
    updatePreviewButton();
    try {
      const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
      const result = await archiveRequest("/" + kind + query);
      if (handleUnavailable(result)) return;
      panel.hidden = false;
      if (handleAuthenticationRequired(result)) return;
      if (result.payload.ok !== true) {
        page.mount.replaceChildren();
        page.cursor = "";
        page.nextButton.hidden = true;
        appendEmptyRow(page.mount, "페이지를 불러오지 못했습니다. 첫 페이지부터 다시 확인하세요.");
        return;
      }
      page.render(result.payload[page.responseField]);
      page.cursor = typeof result.payload.nextCursor === "string"
        ? result.payload.nextCursor
        : "";
      page.nextButton.hidden = !page.cursor;
    } finally {
      page.loading = false;
      updatePreviewButton();
    }
  }

  async function loadAdminPages() {
    await loadRecords();
    if (recordsPanel.hidden) return;
    await loadMetadataPage("participation");
    if (recordsPanel.hidden) return;
    await loadMetadataPage("voice-state-transitions");
    if (recordsPanel.hidden) return;
    await loadMetadataPage("legal-minimal");
    if (recordsPanel.hidden) return;
    await loadFeedbackWorkflows();
  }

  challengeButton.addEventListener("click", async () => {
    if (busy || recordsLoading) return;
    if (!bootstrapNonce) {
      challengeButton.disabled = true;
      status.textContent = "Windows 관리자 런처를 다시 실행해 이 페이지를 여세요.";
      return;
    }
    busy = true;
    challengeButton.disabled = true;
    status.textContent = "등록된 Discord 계정으로 OTP를 보내는 중입니다.";
    try {
      const challengeBody = { bootstrapNonce };
      const challengeRequest = archiveRequest("/challenge", {
        method: "POST",
        body: challengeBody
      });
      bootstrapNonce = "";
      challengeBody.bootstrapNonce = "";
      const result = await challengeRequest;
      if (handleUnavailable(result)) return;
      challengeId = String(result.payload.challengeId || "");
      if (
        result.status >= 200 &&
        result.status < 300 &&
        result.payload.ok === true &&
        challengeId
      ) {
        loginForm.hidden = false;
        loginCode.focus();
        status.textContent = "Discord 1:1 DM의 4자리 코드를 그대로 입력하세요.";
      } else {
        status.textContent = result.status === 429
          ? "OTP 요청 제한에 도달했습니다. 안내된 시간이 지난 뒤 다시 시도하세요."
          : "관리자 증명 또는 OTP 전송을 완료하지 못했습니다.";
      }
    } finally {
      busy = false;
      updatePreviewButton();
    }
  });

  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !challengeId || !/^[A-Za-z0-9]{4}$/.test(loginCode.value)) return;
    busy = true;
    const code = loginCode.value;
    loginCode.value = "";
    try {
      const result = await archiveRequest("/login", {
        method: "POST",
        body: { challengeId, code }
      });
      if (handleUnavailable(result)) return;
      challengeId = "";
      if (result.status >= 200 && result.status < 300 && result.payload.ok === true) {
        await loadAdminPages();
      } else {
        loginForm.hidden = true;
        status.textContent = "OTP 확인에 실패했습니다. 새 코드를 요청하세요.";
      }
    } finally {
      busy = false;
    }
  });

  refreshButton.addEventListener("click", () => {
    if (!busy && !previewToken) void loadRecords("");
  });

  nextButton.addEventListener("click", () => {
    if (busy || recordsLoading || previewToken || !nextCursor) return;
    const cursor = nextCursor;
    nextCursor = "";
    nextButton.hidden = true;
    void loadRecords(cursor);
  });

  for (const [kind, page] of Object.entries(metadataPages)) {
    page.refreshButton.addEventListener("click", () => {
      if (!busy && !previewToken) void loadMetadataPage(kind);
    });
    page.nextButton.addEventListener("click", () => {
      if (busy || page.loading || previewToken || !page.cursor) return;
      const cursor = page.cursor;
      page.cursor = "";
      page.nextButton.hidden = true;
      void loadMetadataPage(kind, cursor);
    });
  }

  previewButton.addEventListener("click", async () => {
    const recordIds = selectedRecordIds();
    if (busy || !recordIds.length) return;
    busy = true;
    updatePreviewButton();
    deleteStatus.textContent = "정확한 삭제 대상을 계산하는 중입니다.";
    try {
      const result = await archiveRequest("/delete/preview", {
        method: "POST",
        body: { recordIds }
      });
      if (handleUnavailable(result)) return;
      if (handleAuthenticationRequired(result)) return;
      previewToken = String(result.payload.previewToken || "");
      if (
        result.status >= 200 &&
        result.status < 300 &&
        result.payload.ok === true &&
        previewToken
      ) {
        const affectedCount = Number(result.payload.affectedCount);
        deleteStatus.textContent = Number.isInteger(affectedCount)
          ? `${affectedCount}개 대상을 확인했습니다. Discord DM의 새 삭제 확인 OTP를 입력하세요.`
          : "대상을 확인했습니다. Discord DM의 새 삭제 확인 OTP를 입력하세요.";
        applyForm.hidden = false;
        deleteCode.focus();
      } else {
        previewToken = "";
        applyForm.hidden = true;
        deleteStatus.textContent = "삭제 미리보기를 만들지 못했습니다.";
      }
    } finally {
      busy = false;
      updatePreviewButton();
    }
  });

  applyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !previewToken || !/^[A-Za-z0-9]{4}$/.test(deleteCode.value)) return;
    busy = true;
    const code = deleteCode.value;
    deleteCode.value = "";
    const token = previewToken;
    previewToken = "";
    try {
      const result = await archiveRequest("/delete/apply", {
        method: "POST",
        body: { previewToken: token, code }
      });
      if (handleUnavailable(result)) return;
      if (handleAuthenticationRequired(result)) return;
      applyForm.hidden = true;
      const applied = result.status >= 200 && result.status < 300 && result.payload.ok === true;
      const deletionState = String(result.payload.state || "");
      deleteStatus.textContent = !applied
        ? "삭제를 적용하지 않았습니다. 새 미리보기와 OTP가 필요합니다."
        : deletionState === "local_fully_purged"
          ? "로컬 원본·백업·파생 사본의 삭제 검증을 완료했습니다."
          : deletionState === "local_cleanup_pending"
            ? "삭제를 접수해 열람에서 숨겼습니다. 백업·파생 사본 정리가 끝날 때까지 완료로 표시하지 않습니다."
            : "삭제 요청은 적용됐지만 완료 상태를 확인할 수 없습니다.";
      if (applied) await loadAdminPages();
    } finally {
      busy = false;
      updatePreviewButton();
    }
  });

  feedbackRefreshButton.addEventListener("click", () => {
    if (!busy) void loadFeedbackWorkflows();
  });

  feedbackCaptureForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = await submitFeedback(
      "/feedback/capture",
      {
        taskId: feedbackFormValue(feedbackCaptureForm, "taskId"),
        sourceRecordId: feedbackFormValue(feedbackCaptureForm, "sourceRecordId"),
        category: feedbackFormValue(feedbackCaptureForm, "category"),
        correction: feedbackFormValue(feedbackCaptureForm, "correction"),
        nonce: feedbackNonce()
      },
      "교정을 원문 결합 후보로 기록했습니다."
    );
    if (payload) {
      feedbackCaptureForm.elements.namedItem("correction").value = "";
      await loadFeedbackWorkflows();
    }
  });

  feedbackGeneralizeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!feedbackGeneralizeForm.elements.namedItem("privacyReviewed").checked) return;
    const privacyReview = {
      schema: "evelyn.feedback-privacy-review.v1",
      reviewedByLocalOperator: true,
      sourceIdentifiersAbsent: true,
      privateDataAbsent: true,
      quotesAbsent: true,
      uniquePhrasesAbsent: true,
      semanticParaphraseRiskAbsent: true,
      styleFingerprintAbsent: true,
      inferenceRiskAbsent: true,
      privacyFixturePassed: true
    };
    const payload = await submitFeedback(
      "/feedback/generalize",
      {
        workflowId: feedbackFormValue(feedbackGeneralizeForm, "workflowId"),
        guidance: feedbackFormValue(feedbackGeneralizeForm, "guidance"),
        privacyReview,
        ancestorVersionIds: []
      },
      "사람이 작성하고 개인정보 검토한 독립 후보를 저장했습니다."
    );
    if (payload) {
      feedbackGeneralizeForm.elements.namedItem("guidance").value = "";
      feedbackGeneralizeForm.elements.namedItem("privacyReviewed").checked = false;
      await loadFeedbackWorkflows();
    }
  });

  feedbackEvaluateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    let report;
    try {
      report = parseFeedbackJson(feedbackFormValue(feedbackEvaluateForm, "report"));
    } catch {
      feedbackStatus.textContent = "평가 report JSON 형식이 올바르지 않습니다.";
      return;
    }
    const payload = await submitFeedback(
      "/feedback/evaluate",
      {
        versionId: feedbackFormValue(feedbackEvaluateForm, "versionId"),
        evalRunId: feedbackFormValue(feedbackEvaluateForm, "evalRunId"),
        baselineContractDigest: feedbackFormValue(feedbackEvaluateForm, "baselineContractDigest"),
        candidateContractDigest: feedbackFormValue(feedbackEvaluateForm, "candidateContractDigest"),
        report
      },
      "고정 24행 평가 게이트 통과를 기록했습니다."
    );
    if (payload) {
      feedbackEvaluateForm.elements.namedItem("report").value = "";
      await loadFeedbackWorkflows();
    }
  });

  feedbackApprovalPreviewForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = await submitFeedback(
      "/feedback/approval/preview",
      { versionId: feedbackFormValue(feedbackApprovalPreviewForm, "versionId") },
      "승인 대상과 평가 결합을 고정했습니다. Discord DM의 새 OTP를 입력하세요."
    );
    if (!payload) return;
    feedbackApprovalPreviewToken = String(payload.previewToken || "");
    feedbackApprovalGuidance.textContent = String(payload.guidance || "");
    feedbackApprovalGuidance.hidden = !feedbackApprovalGuidance.textContent;
    feedbackApprovalApplyForm.hidden = !feedbackApprovalPreviewToken;
  });

  feedbackApprovalApplyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const codeField = feedbackApprovalApplyForm.elements.namedItem("code");
    const token = feedbackApprovalPreviewToken;
    feedbackApprovalPreviewToken = "";
    const payload = await submitFeedback(
      "/feedback/approval/apply",
      { previewToken: token, code: String(codeField.value || "") },
      "독립 후보를 정확히 1회 승인했습니다."
    );
    codeField.value = "";
    feedbackApprovalGuidance.textContent = "";
    feedbackApprovalGuidance.hidden = true;
    feedbackApprovalApplyForm.hidden = true;
    if (payload) await loadFeedbackWorkflows();
  });

  feedbackCanaryForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = await submitFeedback(
      "/feedback/canary",
      {
        versionId: feedbackFormValue(feedbackCanaryForm, "versionId"),
        canaryRunId: feedbackFormValue(feedbackCanaryForm, "canaryRunId"),
        phase: "begin"
      },
      "활성 규칙을 유지한 채 후보 전용 격리 카나리를 시작했습니다. 실제 10건이 끝나면 서버가 자동 집계합니다."
    );
    if (payload) await loadFeedbackWorkflows();
  });

  feedbackActivateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = await submitFeedback(
      "/feedback/activate",
      { versionId: feedbackFormValue(feedbackActivateForm, "versionId") },
      "예상 활성 버전이 일치해 후보를 원자적으로 활성화했습니다."
    );
    if (payload) await loadFeedbackWorkflows();
  });

  feedbackFailureForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = await submitFeedback(
      "/feedback/failure",
      {
        versionId: feedbackFormValue(feedbackFailureForm, "versionId"),
        failureId: feedbackFormValue(feedbackFailureForm, "failureId"),
        taskId: feedbackFormValue(feedbackFailureForm, "taskId"),
        contractVersion: feedbackFormValue(feedbackFailureForm, "contractVersion"),
        evaluatorVersion: feedbackFormValue(feedbackFailureForm, "evaluatorVersion"),
        failureCode: feedbackFormValue(feedbackFailureForm, "failureCode")
      },
      "활성 버전에 결박된 고정 실패 영수증을 기록했습니다."
    );
    if (payload) await loadFeedbackWorkflows();
  });

  feedbackRollbackPreviewForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = await submitFeedback(
      "/feedback/rollback/preview",
      {
        versionId: feedbackFormValue(feedbackRollbackPreviewForm, "versionId"),
        contractVersion: feedbackFormValue(feedbackRollbackPreviewForm, "contractVersion"),
        evaluatorVersion: feedbackFormValue(feedbackRollbackPreviewForm, "evaluatorVersion")
      },
      "현재 실패 영수증과 롤백 대상을 고정했습니다. Discord DM의 새 OTP를 입력하세요."
    );
    if (!payload) return;
    feedbackRollbackPreviewToken = String(payload.previewToken || "");
    feedbackRollbackApplyForm.hidden = !feedbackRollbackPreviewToken;
  });

  feedbackRollbackApplyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const codeField = feedbackRollbackApplyForm.elements.namedItem("code");
    const token = feedbackRollbackPreviewToken;
    feedbackRollbackPreviewToken = "";
    const payload = await submitFeedback(
      "/feedback/rollback/apply",
      { previewToken: token, code: String(codeField.value || "") },
      "검증된 최신 독립 버전으로 1회 롤백했습니다. 자동 재승격하지 않습니다."
    );
    codeField.value = "";
    feedbackRollbackApplyForm.hidden = true;
    if (payload) await loadFeedbackWorkflows();
  });

  feedbackRevokePreviewForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = await submitFeedback(
      "/feedback/revoke/preview",
      {
        versionId: feedbackFormValue(feedbackRevokePreviewForm, "versionId"),
        reason: feedbackFormValue(feedbackRevokePreviewForm, "reason")
      },
      "격리 대상과 모든 후손을 고정했습니다. Discord DM의 새 OTP를 입력하세요."
    );
    if (!payload) return;
    feedbackRevokePreviewToken = String(payload.previewToken || "");
    feedbackRevokeApplyForm.hidden = !feedbackRevokePreviewToken;
  });

  feedbackRevokeApplyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const codeField = feedbackRevokeApplyForm.elements.namedItem("code");
    const token = feedbackRevokePreviewToken;
    feedbackRevokePreviewToken = "";
    const payload = await submitFeedback(
      "/feedback/revoke/apply",
      { previewToken: token, code: String(codeField.value || "") },
      "해당 버전과 그 버전을 조상으로 둔 후손을 격리했습니다."
    );
    codeField.value = "";
    feedbackRevokeApplyForm.hidden = true;
    if (payload) await loadFeedbackWorkflows();
  });

  logoutButton.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    try {
      const result = await archiveRequest("/logout", { method: "POST" });
      if (handleUnavailable(result)) {
        return;
      } else if (handleAuthenticationRequired(result)) {
        status.textContent = "관리자 세션이 이미 종료되었습니다.";
      } else if (result.status >= 200 && result.status < 300) {
        challengeId = "";
        setAuthenticated(false);
        status.textContent = "관리자 세션에서 로그아웃했습니다.";
      }
    } finally {
      busy = false;
    }
  });

  window.addEventListener("pagehide", () => {
    csrfToken = "";
    bootstrapNonce = "";
    challengeId = "";
    previewToken = "";
    feedbackApprovalPreviewToken = "";
    feedbackRollbackPreviewToken = "";
    feedbackRevokePreviewToken = "";
    nextCursor = "";
    loginCode.value = "";
    deleteCode.value = "";
    for (const form of [
      feedbackCaptureForm,
      feedbackGeneralizeForm,
      feedbackEvaluateForm,
      feedbackApprovalApplyForm,
      feedbackCanaryForm,
      feedbackFailureForm,
      feedbackRollbackApplyForm,
      feedbackRevokeApplyForm
    ]) {
      for (const field of form.querySelectorAll("textarea, input[type='password'], input[autocomplete='one-time-code']")) {
        field.value = "";
      }
    }
    feedbackApprovalGuidance.textContent = "";
    setAuthenticated(false);
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) void loadAdminPages();
  });

  document.getElementById("drawer")?.classList.add("open");
  challengeButton.disabled = !bootstrapNonce;
  void loadAdminPages();
})();
