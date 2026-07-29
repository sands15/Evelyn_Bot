(function () {
  "use strict";

  const mount = document.getElementById("runtimeErrorsMount");
  const refreshButton = document.getElementById("runtimeErrorsRefreshButton");
  if (!mount || !refreshButton) return;

  let loading = false;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function formatTime(epochSeconds) {
    const value = Number(epochSeconds) || 0;
    if (!value) return "기록 없음";
    return new Intl.DateTimeFormat("ko-KR", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date(value * 1000));
  }

  function pill(state) {
    const value = String(state || "unknown");
    const node = element("span", "runtime-errors-pill", value);
    node.dataset.state = value;
    return node;
  }

  function renderUnavailable() {
    mount.replaceChildren();
    const summary = element("div", "runtime-errors-summary");
    const copy = element("div");
    copy.append(
      element("strong", "", "오류 상태 대기"),
      element("span", "runtime-errors-meta", "heartbeat 상태를 읽지 못했습니다.")
    );
    summary.append(copy, pill("unavailable"));
    mount.append(
      summary,
      element("span", "runtime-errors-privacy", "메시지·스택·파일 경로는 수집하지 않습니다.")
    );
  }

  function sourceDetail(source) {
    if (!source.available) return source.state === "invalid" ? "상태 파일 손상" : "상태 파일 없음";
    if (source.stale) return "heartbeat 오래됨";
    if (source.lastErrorCode) {
      const errorType = source.lastErrorType ? ` · ${source.lastErrorType}` : "";
      return `${source.lastErrorCode}${errorType} · ${formatTime(source.lastErrorAt)}`;
    }
    return "기록된 오류 없음";
  }

  function render(payload) {
    const errors = payload && payload.errors;
    if (!errors || errors.schema !== "runtime_errors.summary.v1") {
      renderUnavailable();
      return;
    }
    const summaryData = errors.summary || {};
    mount.replaceChildren();

    const summary = element("div", "runtime-errors-summary");
    const copy = element("div");
    copy.append(
      element("div", "runtime-errors-total", `${Number(summaryData.totalCount) || 0}회`),
      element(
        "span",
        "runtime-errors-meta",
        `최근 1시간 ${Number(summaryData.recentErrorCount) || 0}개 소스 · 현재 오류 ${Number(summaryData.currentErrorCount) || 0}개`
      )
    );
    summary.append(copy, pill(errors.state));
    mount.append(summary);

    const sourceList = element("div", "runtime-errors-sources");
    for (const source of Object.values(errors.sources || {})) {
      const row = element("div", "runtime-errors-source");
      const sourceCopy = element("div", "runtime-errors-source-copy");
      sourceCopy.append(
        element("strong", "", source.label || source.id || "runtime"),
        element("span", "runtime-errors-meta", sourceDetail(source))
      );
      row.append(
        sourceCopy,
        element("span", "runtime-errors-source-value", `${Number(source.errorCount) || 0}회`)
      );
      sourceList.append(row);
    }
    mount.append(
      sourceList,
      element("span", "runtime-errors-privacy", "메시지·스택·파일 경로는 수집하지 않습니다.")
    );
  }

  async function refresh() {
    if (loading) return;
    loading = true;
    refreshButton.disabled = true;
    try {
      const response = await fetch("/api/control-page/runtime-errors", { cache: "no-store" });
      render(await response.json());
    } catch (_error) {
      renderUnavailable();
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
  window.setInterval(refresh, 30 * 1000);
})();
