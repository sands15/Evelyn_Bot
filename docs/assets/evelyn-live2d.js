(function () {
  "use strict";

  const MODEL_URL = "./assets/evelyn-avatar/live2d/evelin.model3.json";
  // Calibrated from the center of both eyes inside the visible character bounds.
  const HEAD_FOCUS_X_RATIO = 0.482;
  const HEAD_FOCUS_Y_RATIO = 0.276;
  const HEAD_FOCUS_FULL_DISTANCE_HEIGHT_RATIO = 0.18;
  const HEAD_FOCUS_MIN_DISTANCE_PX = 72;
  const EXPRESSION_PARAMETERS = Object.freeze({
    ulmak: "ParamHairBack75",
    "cat ear": "ParamHairBack65",
    cheek1: "ParamCheek3",
    cheek2: "ParamCheek4",
    dia: "ParamHairBack74",
    "heart eye": "ParamHairBack14",
    mostach: "ParamHairBack64",
    pale: "ParamCheek2",
    puff: "ParamHairBack35",
    "tail down": "ParamHairBack69",
    tear: "ParamCheek5",
    tiered: "ParamHairBack63",
  });
  const HOTKEY_EXPRESSIONS = Object.freeze({
    Digit1: "heart eye",
    Digit2: "dia",
    Digit3: "mostach",
    Digit4: "tiered",
    Digit5: "cheek1",
    Digit6: "cheek2",
    Digit7: "cat ear",
    Digit8: "pale",
    Digit9: "puff",
    Digit0: "tear",
    F1: "tail down",
    F2: "ulmak",
  });
  const IDLE_TAIL_PARAMETERS = Object.freeze([
    { id: "Param_Angle_Rotation15", follow: 0.00, bend: 0.00, phase: 0.00, spring: 10.0, damping: 6.8 },
    { id: "Param_Angle_Rotation9", follow: 0.82, bend: 1.10, phase: 0.36, spring: 9.4, damping: 6.5 },
    { id: "Param_Angle_Rotation10", follow: 0.82, bend: 1.25, phase: 0.74, spring: 8.9, damping: 6.3 },
    { id: "Param_Angle_Rotation11", follow: 0.81, bend: 1.40, phase: 1.14, spring: 8.4, damping: 6.1 },
    { id: "Param_Angle_Rotation12", follow: 0.80, bend: 1.55, phase: 1.56, spring: 7.9, damping: 5.9 },
    { id: "Param_Angle_Rotation13", follow: 0.79, bend: 1.70, phase: 2.00, spring: 7.4, damping: 5.7 },
    { id: "Param_Angle_Rotation14", follow: 0.78, bend: 1.85, phase: 2.46, spring: 6.9, damping: 5.5 },
  ]);

  const state = {
    app: null,
    model: null,
    area: null,
    canvas: null,
    status: null,
    ready: false,
    loading: false,
    speaking: false,
    mouthOpen: 0,
    gazeX: 0,
    gazeY: 0,
    pointerClientX: null,
    pointerClientY: null,
    pointerActive: false,
    activeExpression: null,
    lastExpressionValue: null,
    lastMouthParameter: 0,
    lastEyeOpenParameter: 1,
    idleTailWeight: 1,
    idleTailAngles: IDLE_TAIL_PARAMETERS.map(function () { return 0; }),
    idleTailVelocities: IDLE_TAIL_PARAMETERS.map(function () { return 0; }),
    lastTailRootParameter: 0,
    lastTailTipParameter: 0,
    blinkStartedAt: 0,
    nextBlinkAt: 0,
    resizeObserver: null,
    lastFrameAt: 0,
  };

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function setStatus(message, kind) {
    if (!state.status) return;
    state.status.textContent = message;
    state.status.dataset.kind = kind || "loading";
  }

  function fitModel() {
    if (!state.ready || !state.model || !state.app || !state.area) return;
    const rect = state.area.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width));
    const height = Math.max(1, Math.round(rect.height));
    state.app.renderer.resize(width, height);

    state.model.scale.set(1);
    const modelWidth = Math.max(1, state.model.width);
    const modelHeight = Math.max(1, state.model.height);
    const scale = Math.min((width * 0.96) / modelWidth, (height * 1.02) / modelHeight);
    state.model.scale.set(scale);
    state.model.x = width * 0.52;
    state.model.y = height - (modelHeight * scale * 0.5) + height * 0.01;
    if (state.pointerActive) updateGazeFromPointer();
  }

  function scheduleNextBlink(now) {
    state.nextBlinkAt = now + 2400 + Math.random() * 3600;
  }

  function eyeOpenValue(now) {
    if (!state.nextBlinkAt) scheduleNextBlink(now);
    if (!state.blinkStartedAt && now >= state.nextBlinkAt) {
      state.blinkStartedAt = now;
    }
    if (!state.blinkStartedAt) return 1;

    const elapsed = now - state.blinkStartedAt;
    if (elapsed >= 170) {
      state.blinkStartedAt = 0;
      scheduleNextBlink(now);
      return 1;
    }
    if (elapsed < 70) return 1 - elapsed / 70;
    if (elapsed < 105) return 0;
    return (elapsed - 105) / 65;
  }

  function speechTarget(now) {
    if (!state.speaking) return 0;
    const syllable = Math.max(0, Math.sin(now * 0.020));
    const secondary = Math.max(0, Math.sin(now * 0.033 + 1.8));
    const pause = Math.sin(now * 0.0047) > -0.78 ? 1 : 0.18;
    return clamp((0.18 + syllable * 0.58 + secondary * 0.22) * pause, 0, 1);
  }

  function updateIdleTail(coreModel, now, elapsed) {
    const targetWeight = state.speaking ? 0 : 1;
    const smoothing = 1 - Math.pow(0.9, elapsed);
    state.idleTailWeight += (targetWeight - state.idleTailWeight) * smoothing;

    const frameSeconds = clamp(elapsed / 60, 1 / 240, 1 / 20);
    const drivePhase = now * 0.00045 + Math.sin(now * 0.000055 + 0.4) * 0.12;
    const rootTarget = Math.sin(drivePhase) * 8.8;

    IDLE_TAIL_PARAMETERS.forEach(function (parameter, index) {
      const previousAngle = index > 0 ? state.idleTailAngles[index - 1] : 0;
      const travelingBend = index > 0
        ? Math.sin(drivePhase - parameter.phase) * parameter.bend
        : 0;
      const targetAngle = index === 0
        ? rootTarget
        : previousAngle * parameter.follow + travelingBend;
      const springForce = (targetAngle - state.idleTailAngles[index]) * parameter.spring;
      state.idleTailVelocities[index] += springForce * frameSeconds;
      state.idleTailVelocities[index] *= Math.exp(-parameter.damping * frameSeconds);
      state.idleTailAngles[index] += state.idleTailVelocities[index] * frameSeconds;
      state.idleTailAngles[index] = clamp(state.idleTailAngles[index], -13.5, 13.5);

      const value = state.idleTailAngles[index] * state.idleTailWeight;
      setCoreParameter(coreModel, parameter.id, value);
    });
  }

  function setCoreParameter(coreModel, id, value) {
    try {
      const parameterId = resolveCoreParameterId(id);
      coreModel.setParameterValueById(parameterId, value);
    } catch (_error) {
      // A model revision may omit an optional parameter. Keep rendering the rest.
    }
  }

  function resolveCoreParameterId(id) {
    return state.model && state.model.internalModel && state.model.internalModel.getIdSafe
      ? state.model.internalModel.getIdSafe(id)
      : id;
  }

  function updateModelParameters() {
    if (!state.model || !state.model.internalModel) return;
    const now = performance.now();
    const elapsed = state.lastFrameAt ? clamp((now - state.lastFrameAt) / 16.667, 0.25, 4) : 1;
    state.lastFrameAt = now;
    const coreModel = state.model.internalModel.coreModel;
    const eyeOpen = eyeOpenValue(now);
    const mouthTarget = speechTarget(now);
    const smoothing = 1 - Math.pow(0.58, elapsed);
    state.mouthOpen += (mouthTarget - state.mouthOpen) * smoothing;

    setCoreParameter(coreModel, "ParamEyeLOpen", eyeOpen);
    setCoreParameter(coreModel, "ParamEyeROpen", eyeOpen);
    setCoreParameter(coreModel, "ParamMouthOpenY", state.mouthOpen);
    if (state.speaking) {
      setCoreParameter(coreModel, "ParamMouthForm", 0.18);
    }
    updateIdleTail(coreModel, now, elapsed);

    try {
      state.lastMouthParameter = coreModel.getParameterValueById(resolveCoreParameterId("ParamMouthOpenY"));
      state.lastEyeOpenParameter = coreModel.getParameterValueById(resolveCoreParameterId("ParamEyeLOpen"));
      state.lastTailRootParameter = coreModel.getParameterValueById(
        resolveCoreParameterId(IDLE_TAIL_PARAMETERS[0].id)
      );
      state.lastTailTipParameter = coreModel.getParameterValueById(
        resolveCoreParameterId(IDLE_TAIL_PARAMETERS[IDLE_TAIL_PARAMETERS.length - 1].id)
      );
      const expressionId = EXPRESSION_PARAMETERS[state.activeExpression];
      state.lastExpressionValue = expressionId
        ? coreModel.getParameterValueById(resolveCoreParameterId(expressionId))
        : null;
    } catch (_error) {
      // Diagnostics should never interrupt rendering.
    }

  }

  function updateLive2DFrame(ticker) {
    if (state.model) state.model.update(ticker.deltaMS);
  }

  function updateFocus() {
    if (!state.model || !state.model.internalModel) return;
    state.model.internalModel.focusController.focus(state.gazeX, state.gazeY);
  }

  function getModelHeadClientPoint() {
    if (!state.model || !state.app || !state.area) return null;
    const rect = state.area.getBoundingClientRect();
    const screen = state.app.screen || {};
    const screenWidth = Math.max(1, Number(screen.width) || Math.round(rect.width) || 1);
    const screenHeight = Math.max(1, Number(screen.height) || Math.round(rect.height) || 1);
    const modelWidth = Math.max(1, Number(state.model.width) || 1);
    const modelHeight = Math.max(1, Number(state.model.height) || 1);
    const anchorX = state.model.anchor ? Number(state.model.anchor.x) : 0.5;
    const anchorY = state.model.anchor ? Number(state.model.anchor.y) : 0.5;
    const headStageX = state.model.x + (HEAD_FOCUS_X_RATIO - anchorX) * modelWidth;
    const headStageY = state.model.y + (HEAD_FOCUS_Y_RATIO - anchorY) * modelHeight;
    const cssScaleX = rect.width / screenWidth;
    const cssScaleY = rect.height / screenHeight;
    return {
      clientX: rect.left + headStageX * cssScaleX,
      clientY: rect.top + headStageY * cssScaleY,
      fullDistance: Math.max(
        HEAD_FOCUS_MIN_DISTANCE_PX,
        modelHeight * cssScaleY * HEAD_FOCUS_FULL_DISTANCE_HEIGHT_RATIO
      ),
    };
  }

  function updateGazeFromPointer() {
    if (!state.pointerActive) return;
    const head = getModelHeadClientPoint();
    if (!head) return;
    const dx = state.pointerClientX - head.clientX;
    const dy = head.clientY - state.pointerClientY;
    const distance = Math.hypot(dx, dy);
    if (distance < 0.5) {
      state.gazeX = 0;
      state.gazeY = 0;
    } else {
      const strength = clamp(distance / head.fullDistance, 0, 1);
      state.gazeX = clamp((dx / distance) * strength, -1, 1);
      state.gazeY = clamp((dy / distance) * strength, -1, 1);
    }
    updateFocus();
  }

  function onPointerMove(event) {
    state.pointerClientX = event.clientX;
    state.pointerClientY = event.clientY;
    state.pointerActive = true;
    updateGazeFromPointer();
  }

  function resetFocus() {
    state.pointerClientX = null;
    state.pointerClientY = null;
    state.pointerActive = false;
    state.gazeX = 0;
    state.gazeY = 0;
    updateFocus();
  }

  function shouldIgnoreHotkey(event) {
    const target = event.target;
    if (!target || !(target instanceof Element)) return false;
    return Boolean(target.closest("input, textarea, select, [contenteditable='true']"));
  }

  function onHotkey(event) {
    if (shouldIgnoreHotkey(event)) return;
    if (event.code === "Escape") {
      controller.clearExpression();
      return;
    }
    const expression = HOTKEY_EXPRESSIONS[event.code];
    if (!expression) return;
    event.preventDefault();
    controller.setExpression(expression);
  }

  function bindInteractions() {
    window.addEventListener("pointermove", onPointerMove, { passive: true });
    window.addEventListener("blur", resetFocus);
    document.addEventListener("pointerleave", resetFocus);
    window.addEventListener("keydown", onHotkey);
  }

  function markUnavailable(error) {
    const message = error && error.message ? error.message : String(error || "unknown error");
    state.ready = false;
    if (state.area) {
      state.area.classList.remove("live2d-ready");
      state.area.classList.add("live2d-error");
    }
    if (state.app && typeof state.app.stop === "function") state.app.stop();
    setStatus("Live2D 폴백 · " + message, "error");
    console.error("[Evelyn Live2D]", error);
  }

  function onLive2dRuntimeError(event) {
    const source = String(event.filename || "");
    const message = String(event.message || "Live2D runtime error");
    if (!source.includes("/assets/vendor/live2d/") && !source.includes("/assets/evelyn-live2d.js")) return;
    markUnavailable(event.error || new Error(message));
  }

  async function initialize() {
    if (state.loading || state.ready) return state.ready;
    state.area = document.getElementById("evelynLive2dArea");
    state.canvas = document.getElementById("evelynLive2dCanvas");
    state.status = document.getElementById("evelynLive2dStatus");
    if (!state.area || !state.canvas) return false;

    state.loading = true;
    window.addEventListener("error", onLive2dRuntimeError);
    setStatus("Live2D 모델 불러오는 중", "loading");
    try {
      if (!window.PIXI || !window.PIXI.live2d || !window.PIXI.live2d.Live2DModel) {
        throw new Error("Live2D runtime unavailable");
      }
      if (window.PIXI.extensions && window.PIXI.live2d.Live2DPlugin) {
        window.PIXI.extensions.add(window.PIXI.live2d.Live2DPlugin);
      }
      if (window.PIXI.live2d.configureCubismSDK) {
        window.PIXI.live2d.configureCubismSDK({ memorySizeMB: 32 });
      }
      state.app = new window.PIXI.Application();
      await state.app.init({
        canvas: state.canvas,
        width: Math.max(1, state.area.clientWidth),
        height: Math.max(1, state.area.clientHeight),
        backgroundAlpha: 0,
        antialias: true,
        autoDensity: true,
        resolution: Math.min(window.devicePixelRatio || 1, 2),
        powerPreference: "high-performance",
        preference: "webgl",
      });
      state.app.ticker.maxFPS = 60;
      state.model = await window.PIXI.live2d.Live2DModel.from(MODEL_URL, {
        autoHitTest: false,
        autoFocus: false,
        autoUpdate: false,
      });
      state.model.anchor.set(0.5, 0.5);
      state.model.internalModel.on("beforeModelUpdate", updateModelParameters);
      state.app.stage.addChild(state.model);
      state.app.ticker.add(updateLive2DFrame);
      state.ready = true;
      state.area.classList.remove("live2d-error");
      state.area.classList.add("live2d-ready");
      state.area.dataset.live2dExpression = "none";
      fitModel();
      bindInteractions();
      scheduleNextBlink(performance.now());
      state.resizeObserver = new ResizeObserver(fitModel);
      state.resizeObserver.observe(state.area);
      state.canvas.addEventListener("webglcontextlost", (event) => {
        event.preventDefault();
        markUnavailable(new Error("WebGL context lost"));
      });
      setStatus("Live2D 연결됨 · 1–0/F1 표정", "ready");
      window.dispatchEvent(new CustomEvent("evelyn-live2d-ready"));
      return true;
    } catch (error) {
      markUnavailable(error);
      return false;
    } finally {
      state.loading = false;
    }
  }

  const controller = {
    init: initialize,
    isReady: function () {
      return state.ready;
    },
    setSpeaking: function (speaking) {
      state.speaking = Boolean(speaking);
      if (state.area) state.area.classList.toggle("live2d-speaking", state.speaking);
    },
    setExpression: function (name) {
      const normalized = String(name || "").trim().toLowerCase();
      if (!Object.prototype.hasOwnProperty.call(EXPRESSION_PARAMETERS, normalized)) return false;
      if (state.activeExpression === normalized) {
        controller.clearExpression();
        return true;
      }
      state.activeExpression = normalized;
      if (state.area) state.area.dataset.live2dExpression = state.activeExpression || "none";
      if (state.model) {
        state.model.expression(normalized).catch((error) => {
          console.warn("[Evelyn Live2D] expression failed", normalized, error);
        });
      }
      return true;
    },
    clearExpression: function () {
      state.activeExpression = null;
      if (state.area) state.area.dataset.live2dExpression = "none";
      const manager = state.model
        && state.model.internalModel
        && state.model.internalModel.motionManager
        && state.model.internalModel.motionManager.expressionManager;
      if (manager) {
        manager.resetExpression();
        manager.currentExpression = manager.defaultExpression;
      }
    },
    availableExpressions: function () {
      return Object.keys(EXPRESSION_PARAMETERS);
    },
    snapshot: function () {
      const headFocus = getModelHeadClientPoint();
      return {
        ready: state.ready,
        speaking: state.speaking,
        mouthOpen: Number(state.mouthOpen.toFixed(3)),
        mouthParameter: Number(state.lastMouthParameter.toFixed(3)),
        eyeOpenParameter: Number(state.lastEyeOpenParameter.toFixed(3)),
        idleTailWeight: Number(state.idleTailWeight.toFixed(3)),
        tailRootParameter: Number(state.lastTailRootParameter.toFixed(3)),
        tailTipParameter: Number(state.lastTailTipParameter.toFixed(3)),
        expression: state.activeExpression,
        expressionParameter: state.lastExpressionValue == null
          ? null
          : Number(state.lastExpressionValue.toFixed(3)),
        gaze: {
          x: Number(state.gazeX.toFixed(3)),
          y: Number(state.gazeY.toFixed(3)),
          pointerActive: state.pointerActive,
          headClientX: headFocus ? Number(headFocus.clientX.toFixed(1)) : null,
          headClientY: headFocus ? Number(headFocus.clientY.toFixed(1)) : null,
        },
        modelUrl: MODEL_URL,
        canvas: state.canvas ? {
          width: state.canvas.width,
          height: state.canvas.height,
        } : null,
        model: state.model ? {
          x: state.model.x,
          y: state.model.y,
          width: state.model.width,
          height: state.model.height,
          scale: state.model.scale.x,
          internalWidth: state.model.internalModel && state.model.internalModel.width,
          internalHeight: state.model.internalModel && state.model.internalModel.height,
          originalWidth: state.model.internalModel && state.model.internalModel.originalWidth,
          originalHeight: state.model.internalModel && state.model.internalModel.originalHeight,
        } : null,
      };
    },
  };

  window.EvelynLive2D = controller;
  initialize();
})();
