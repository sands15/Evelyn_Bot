(function () {
  const CHECKS = [
    ["controlReady", "Control-Page"],
    ["botReady", "Bot API"],
    ["mainReady", "Main LLM"],
    ["routerReady", "Router LLM"],
    ["subReady", "Sub LLM"],
    ["ttsReady", "TTS"],
    ["sttReady", "STT"],
  ];

  function clampPercent(value) {
    return Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
  }

  function runtimeServices(payload) {
    return (((payload || {}).runtime || {}).services) || {};
  }

  function hasReadyRuntimeServices(payload) {
    const services = runtimeServices(payload);
    return Boolean(
      payload &&
      payload.ok !== false &&
      services.botReady &&
      services.mainReady &&
      services.routerReady &&
      services.subReady &&
      services.ttsReady
    );
  }

  function fromRuntimeServices(payload) {
    const services = runtimeServices(payload);
    const steps = CHECKS.map(([key, label]) => ({
      key,
      label,
      done: Boolean(services[key]),
      status: services[key] ? "done" : "pending",
    }));
    const done = steps.filter((step) => step.done).length;
    const total = Math.max(1, steps.length);
    const percent = clampPercent((done / total) * 100);
    const current = steps.find((step) => !step.done) || steps[steps.length - 1];
    return {
      percent,
      phase: percent >= 100 ? "Control Ready" : "Waiting for " + current.label,
      ready: percent >= 100,
      componentsReady: percent >= 100,
      done,
      total,
      source: "runtime_services_fallback",
      steps,
    };
  }

  function progressFromPayload(payload) {
    return (payload && (payload.bootProgress || ((payload.runtime || {}).bootProgress))) || fromRuntimeServices(payload);
  }

  function isReady(payload) {
    const progress = progressFromPayload(payload);
    if (!progress || typeof progress !== "object") return false;
    const percent = clampPercent(progress.percent);
    return (percent >= 100 && progress.ready !== false)
      || progress.componentsReady === true
      || hasReadyRuntimeServices(payload);
  }

  window.EvelynBootProgress = {
    clampPercent,
    fromRuntimeServices,
    hasReadyRuntimeServices,
    isReady,
    progressFromPayload,
  };
}());
