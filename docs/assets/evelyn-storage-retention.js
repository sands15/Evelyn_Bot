(function () {
  "use strict";

  const mount = document.getElementById("storageRetentionMount");
  const refreshButton = document.getElementById("storageRetentionRefreshButton");
  if (!mount || !refreshButton) return;

  const labels = {
    runtimeArtifacts: "런타임 산출물",
    hostLogs: "호스트 로그",
    voiceDebug: "음성 디버그",
  };
  let loading = false;

  function formatBytes(value) {
    const bytes = Math.max(0, Number(value) || 0);
    if (bytes < 1024) return `${Math.round(bytes)} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let amount = bytes / 1024;
    let index = 0;
    while (amount >= 1024 && index < units.length - 1) {
      amount /= 1024;
      index += 1;
    }
    return `${amount >= 10 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
  }

  function formatTime(epochSeconds) {
    const value = Number(epochSeconds) || 0;
    if (!value) return "기록 없음";
    return new Intl.DateTimeFormat("ko-KR", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value * 1000));
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderUnavailable(payload) {
    mount.replaceChildren();
    const summary = element("div", "storage-retention-summary");
    const copy = element("div");
    copy.append(
      element("strong", "", "보고서 대기"),
      element(
        "span",
        "storage-retention-meta",
        payload && payload.error === "storage_retention_report_missing"
          ? "Host Supervisor가 첫 dry-run 보고서를 만들면 여기에 표시됩니다."
          : "저장공간 보고서를 읽지 못했습니다."
      )
    );
    const pill = element("span", "storage-retention-pill", "unavailable");
    pill.dataset.state = "unavailable";
    summary.append(copy, pill);
    mount.append(summary, element("span", "storage-retention-policy", "자동 삭제 꺼짐 · 보고만 수행"));
  }

  function render(payload) {
    if (!payload || !payload.available || !payload.report) {
      renderUnavailable(payload);
      return;
    }
    const report = payload.report;
    const summaryData = report.summary || {};
    const state = payload.stale ? "stale" : String(report.state || "unknown");
    mount.replaceChildren();

    const summary = element("div", "storage-retention-summary");
    const copy = element("div");
    copy.append(
      element(
        "div",
        "storage-retention-total",
        `${Number(summaryData.candidateCount) || 0}개 · ${formatBytes(summaryData.candidateBytes)}`
      ),
      element(
        "span",
        "storage-retention-meta",
        `${formatTime(report.generatedAt)} 검사${payload.stale ? " · 오래된 보고서" : ""}`
      )
    );
    const pill = element("span", "storage-retention-pill", state);
    pill.dataset.state = state;
    summary.append(copy, pill);
    mount.append(summary);

    const scopes = element("div", "storage-retention-scopes");
    for (const scopeId of ["runtimeArtifacts", "hostLogs", "voiceDebug"]) {
      const scope = (report.scopes || {})[scopeId] || {};
      const row = element("div", "storage-retention-scope");
      const scopeCopy = element("div", "storage-retention-scope-copy");
      scopeCopy.append(
        element("strong", "", labels[scopeId] || scopeId),
        element(
          "span",
          scope.state === "error" ? "storage-retention-error" : "storage-retention-meta",
          scope.state === "absent"
            ? "폴더 없음"
            : scope.state === "error"
              ? "스캔 실패"
              : `${Number(scope.trackedCount) || 0}개 항목 추적`
        )
      );
      row.append(
        scopeCopy,
        element(
          "span",
          "storage-retention-scope-value",
          `${Number(scope.candidateCount) || 0}개 · ${formatBytes(scope.candidateBytes)}`
        )
      );
      scopes.append(row);
    }
    mount.append(scopes, element("span", "storage-retention-policy", "자동 삭제 꺼짐 · 보고만 수행"));
  }

  async function refresh() {
    if (loading) return;
    loading = true;
    refreshButton.disabled = true;
    try {
      const response = await fetch("/api/control-page/storage-retention", { cache: "no-store" });
      const payload = await response.json();
      render(payload);
    } catch (_error) {
      renderUnavailable({ error: "request_failed" });
    } finally {
      loading = false;
      refreshButton.disabled = false;
    }
  }

  refreshButton.addEventListener("click", refresh);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) refresh();
  });
  refresh();
  window.setInterval(refresh, 60 * 1000);
})();
