(function () {
  "use strict";

  const MODEL_URL = "./assets/evelyn-avatar/live2d/evelin.model3.json";
  const CAT_EAR_PARAMETER = "ParamHairBack65";
  const CAT_EAR_PART = "ear";
  const CAT_TAIL_PART = "Part2";
  const CAT_ACCESSORY_HOTKEY = "Digit7";
  const MODEL_STATE_STORAGE_KEY = "evelynLive2dModelStateV1";
  const SPEECH_EXPRESSION_COOLDOWN_MS = 9000;
  const NATURAL_BREATH_CYCLE_SECONDS = 9.6;
  const IDLE_TAIL_TIME_SCALE = 3.75;
  const CHEST_WARP_REFERENCE_DRAWABLE = "Bra";
  const CHEST_WARP_DRAWABLE_IDS = new Set([
    "ArtMesh120",
    "ArtMesh121",
    "Bra",
    "ArtMesh126",
    "ArtMesh127",
  ]);
  const CHEST_WARP_SWEATER_DRAWABLE_IDS = new Set([
    "ArtMesh120",
    "ArtMesh121",
  ]);
  const CHEST_WARP_SWEATER_SCALE = 0.7;
  const CHEST_WARP_GLOBAL_SCALE = 1;
  const CHEST_WARP_LIFT = 0.016;
  const CHEST_WARP_OUTWARD = 0.009;
  const SPEECH_EXPRESSION_PARAMETERS = Object.freeze({
    "heart eye": "ParamHairBack14",
    dia: "ParamHairBack74",
    cheek1: "ParamCheek3",
    cheek2: "ParamCheek4",
    pale: "ParamCheek2",
    puff: "ParamHairBack35",
    tear: "ParamCheek5",
  });
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
    Digit8: "pale",
    Digit9: "puff",
    Digit0: "tear",
    F1: "tail down",
    F2: "ulmak",
  });
  const IDLE_TAIL_PARAMETERS = Object.freeze([
    { id: "Param_Angle_Rotation15", follow: 0.00, gain: 1.0, spring: 10.0, damping: 6.8 },
    { id: "Param_Angle_Rotation9", follow: 1.04, gain: 2.0, spring: 28.2, damping: 6.5 },
    { id: "Param_Angle_Rotation10", follow: 1.06, gain: 2.5, spring: 26.7, damping: 6.3 },
    { id: "Param_Angle_Rotation11", follow: 1.08, gain: 3.0, spring: 25.2, damping: 6.1 },
    { id: "Param_Angle_Rotation12", follow: 1.10, gain: 3.5, spring: 23.7, damping: 5.9 },
    { id: "Param_Angle_Rotation13", follow: 1.12, gain: 4.0, spring: 22.2, damping: 5.7 },
    { id: "Param_Angle_Rotation14", follow: 1.15, gain: 4.0, spring: 20.7, damping: 5.5 },
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
    speechExpression: null,
    speechExpressionWeight: 0,
    lastSpeechExpressionAt: 0,
    lastSpeechText: "",
    mouthOpen: 0,
    gazeX: 0,
    gazeY: 0,
    avatarDirector: null,
    avatarDirectorSnapshot: null,
    avatarDirectorErrorReported: false,
    catAccessoriesVisible: true,
    activeExpression: null,
    lastExpressionValue: null,
    lastMouthParameter: 0,
    lastEyeOpenParameter: 1,
    lastBreathParameter: 0,
    breathConfigured: false,
    chestWarpDrawableIndices: [],
    chestWarpDrawableScales: new Map(),
    chestWarpReferenceIndex: -1,
    lastChestWarpInhale: 0,
    lastChestWarpMaxLift: 0,
    lastChestWarpMaxOutward: 0,
    lastCatEarParameter: 1,
    lastCatEarPartOpacity: 1,
    lastCatTailPartOpacity: 1,
    idleTailWeight: 1,
    idleTailAngles: IDLE_TAIL_PARAMETERS.map(function () { return 0; }),
    idleTailVelocities: IDLE_TAIL_PARAMETERS.map(function () { return 0; }),
    idleTailParameters: IDLE_TAIL_PARAMETERS.map(function () { return 0; }),
    lastTailRootParameter: 0,
    lastTailTipParameter: 0,
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

  function restorePersistedModelState() {
    try {
      const raw = window.localStorage.getItem(MODEL_STATE_STORAGE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (!saved || typeof saved !== "object" || saved.version !== 1) return;
      state.catAccessoriesVisible = typeof saved.catAccessoriesVisible === "boolean"
        ? saved.catAccessoriesVisible
        : true;
      state.activeExpression = typeof saved.activeExpression === "string"
        && Object.prototype.hasOwnProperty.call(EXPRESSION_PARAMETERS, saved.activeExpression)
        ? saved.activeExpression
        : null;
    } catch (_error) {
      try {
        window.localStorage.removeItem(MODEL_STATE_STORAGE_KEY);
      } catch (_storageError) {
        // Storage may be unavailable in private or restricted browser contexts.
      }
    }
  }

  function persistModelState() {
    try {
      window.localStorage.setItem(MODEL_STATE_STORAGE_KEY, JSON.stringify({
        version: 1,
        activeExpression: state.activeExpression,
        catAccessoriesVisible: state.catAccessoriesVisible,
      }));
    } catch (_error) {
      // Persistence is optional; model controls must keep working without storage.
    }
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
    const drivePhase = now * 0.00045 * IDLE_TAIL_TIME_SCALE
      + Math.sin(now * 0.000055 + 0.4) * 0.12;
    const rootTarget = Math.sin(drivePhase) * 6.5;

    IDLE_TAIL_PARAMETERS.forEach(function (parameter, index) {
      const targetAngle = index === 0
        ? rootTarget
        : state.idleTailAngles[index - 1] * parameter.follow;
      const springForce = (targetAngle - state.idleTailAngles[index])
        * parameter.spring
        * IDLE_TAIL_TIME_SCALE
        * IDLE_TAIL_TIME_SCALE;
      state.idleTailVelocities[index] += springForce * frameSeconds;
      state.idleTailVelocities[index] *= Math.exp(
        -parameter.damping * IDLE_TAIL_TIME_SCALE * frameSeconds
      );
      state.idleTailAngles[index] += state.idleTailVelocities[index] * frameSeconds;
      state.idleTailAngles[index] = clamp(state.idleTailAngles[index], -13.5, 13.5);

      const localAngle = index === 0
        ? state.idleTailAngles[0]
        : state.idleTailAngles[index] - state.idleTailAngles[index - 1];
      try {
        const parameterId = resolveCoreParameterId(parameter.id);
        const physicsValue = coreModel.getParameterValueById(parameterId);
        const idleValue = clamp(localAngle * parameter.gain, -8, 8);
        const value = physicsValue + (idleValue - physicsValue) * state.idleTailWeight;
        coreModel.setParameterValueById(parameterId, value);
        state.idleTailParameters[index] = coreModel.getParameterValueById(parameterId);
      } catch (_error) {
        // A model revision may omit an optional tail segment.
      }
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

  function enforceCatAccessoryVisibility(coreModel) {
    const opacity = state.catAccessoriesVisible ? 1 : 0;
    setCoreParameter(coreModel, CAT_EAR_PARAMETER, opacity);
    try {
      coreModel.setPartOpacityById(resolveCoreParameterId(CAT_EAR_PART), opacity);
      coreModel.setPartOpacityById(resolveCoreParameterId(CAT_TAIL_PART), opacity);
    } catch (_error) {
      // Keep supporting model revisions that expose the parameter without named parts.
    }
  }

  function applyActiveExpression() {
    if (state.area) state.area.dataset.live2dExpression = state.activeExpression || "none";
    if (!state.model || !state.activeExpression) return;
    state.model.expression(state.activeExpression).catch((error) => {
      console.warn("[Evelyn Live2D] expression failed", state.activeExpression, error);
    });
  }

  function chooseSpeechExpression(text) {
    const normalized = String(text || "").trim().toLowerCase();
    if (/(사랑|좋아해|소중|보고 싶|❤|♥|💕|😍|love|adore)/i.test(normalized)) {
      return "heart eye";
    }
    if (/(기뻐|행복|최고|멋지|고마|반가|잘했|ㅋㅋ|ㅎㅎ|하하|😊|happy|great|awesome|thank|glad)/i.test(normalized)) {
      return "dia";
    }
    if (/(슬프|미안|죄송|울고|눈물|속상|아프다|아파|sad|sorry|cry|hurt)/i.test(normalized)) {
      return "tear";
    }
    if (/(싫어|화나|짜증|흥[.!?… ]|삐졌|답답|annoy|angry|mad|upset)/i.test(normalized)) {
      return "puff";
    }
    if (/(걱정|불안|무서|위험|실패|오류|문제|경고|못 찾|안 돼|worry|afraid|danger|error|failed|warning)/i.test(normalized)) {
      return "pale";
    }
    return null;
  }

  function configureNaturalBreathing() {
    const internalModel = state.model && state.model.internalModel;
    const breath = internalModel && internalModel.breath;
    const parameters = breath && typeof breath.getParameters === "function"
      ? breath.getParameters()
      : null;
    if (!parameters || typeof parameters.getSize !== "function") return false;

    let dedicatedBreathFound = false;
    for (let index = 0; index < parameters.getSize(); index += 1) {
      const parameter = parameters.at(index);
      const isDedicatedBreath = Boolean(
        parameter
        && parameter.parameterId
        && (
          parameter.parameterId === internalModel.idParamBreath
          || (
            typeof parameter.parameterId.isEqual === "function"
            && parameter.parameterId.isEqual(internalModel.idParamBreath)
          )
        )
      );
      if (isDedicatedBreath) {
        // A smooth 0..1 chest cycle runs before physics, so hair, ribbons and
        // bust physics receive the same inhale/exhale signal as the torso rig.
        parameter.offset = 0.5;
        parameter.peak = 0.5;
        parameter.cycle = NATURAL_BREATH_CYCLE_SECONDS;
        parameter.weight = 1;
        parameter.waveform = "triangle";
        dedicatedBreathFound = true;
      } else if (parameter) {
        // Head and body-angle sine waves caused the previous figure-eight idle.
        // Avatar Director owns those channels.
        parameter.offset = 0;
        parameter.peak = 0;
      }
    }
    state.breathConfigured = dedicatedBreathFound;
    return dedicatedBreathFound;
  }

  function configureChestMeshWarp() {
    if (!state.model || !state.model.internalModel) return false;
    const coreModel = state.model.internalModel.coreModel;
    state.chestWarpDrawableIndices = [];
    state.chestWarpDrawableScales.clear();
    state.chestWarpReferenceIndex = -1;
    for (let index = 0; index < coreModel.getDrawableCount(); index += 1) {
      const drawableId = coreModel.getDrawableId(index).getString().s;
      if (CHEST_WARP_DRAWABLE_IDS.has(drawableId)) {
        state.chestWarpDrawableIndices.push(index);
        state.chestWarpDrawableScales.set(
          index,
          CHEST_WARP_SWEATER_DRAWABLE_IDS.has(drawableId)
            ? CHEST_WARP_SWEATER_SCALE
            : 1
        );
      }
      if (drawableId === CHEST_WARP_REFERENCE_DRAWABLE) {
        state.chestWarpReferenceIndex = index;
      }
    }
    return state.chestWarpReferenceIndex >= 0 && state.chestWarpDrawableIndices.length > 0;
  }

  function smoothstep01(value) {
    const normalized = clamp(value, 0, 1);
    return normalized * normalized * (3 - 2 * normalized);
  }

  function applyNaturalChestMeshWarp() {
    if (
      !state.model
      || !state.model.internalModel
      || state.chestWarpReferenceIndex < 0
      || state.chestWarpDrawableIndices.length === 0
    ) {
      return;
    }
    const coreModel = state.model.internalModel.coreModel;
    let breathValue = 0;
    try {
      breathValue = clamp(
        coreModel.getParameterValueById(resolveCoreParameterId("ParamBreath")),
        0,
        1
      );
    } catch (_error) {
      return;
    }
    // ParamBreath is already a linear triangle wave. Keep the temporal value
    // linear so inhale/exhale reverse immediately instead of pausing at either end.
    const inhale = breathValue;
    const referenceVertices = coreModel.getDrawableVertexPositions(state.chestWarpReferenceIndex);
    let referenceMinX = Infinity;
    let referenceMaxX = -Infinity;
    let referenceMinY = Infinity;
    let referenceMaxY = -Infinity;
    for (let index = 0; index < referenceVertices.length; index += 2) {
      referenceMinX = Math.min(referenceMinX, referenceVertices[index]);
      referenceMaxX = Math.max(referenceMaxX, referenceVertices[index]);
      referenceMinY = Math.min(referenceMinY, referenceVertices[index + 1]);
      referenceMaxY = Math.max(referenceMaxY, referenceVertices[index + 1]);
    }
    const centerX = (referenceMinX + referenceMaxX) * 0.5;
    const centerY = referenceMinY + (referenceMaxY - referenceMinY) * 0.53;
    const radiusX = Math.max(0.12, (referenceMaxX - referenceMinX) * 0.72);
    const radiusY = Math.max(0.085, (referenceMaxY - referenceMinY) * 0.60);
    let maxLift = 0;
    let maxOutward = 0;

    state.chestWarpDrawableIndices.forEach(function (drawableIndex) {
      const vertices = coreModel.getDrawableVertexPositions(drawableIndex);
      const drawableScale = state.chestWarpDrawableScales.get(drawableIndex) || 1;
      for (let vertexIndex = 0; vertexIndex < vertices.length; vertexIndex += 2) {
        const normalizedX = (vertices[vertexIndex] - centerX) / radiusX;
        const normalizedY = (vertices[vertexIndex + 1] - centerY) / radiusY;
        const distanceSquared = normalizedX * normalizedX + normalizedY * normalizedY;
        if (distanceSquared >= 1) continue;
        const weight = smoothstep01(1 - distanceSquared);
        const lift = CHEST_WARP_LIFT
          * CHEST_WARP_GLOBAL_SCALE
          * inhale
          * weight
          * drawableScale;
        const outward = CHEST_WARP_OUTWARD
          * CHEST_WARP_GLOBAL_SCALE
          * inhale
          * weight
          * drawableScale
          * (0.35 + Math.min(1, Math.abs(normalizedX)) * 0.65);
        vertices[vertexIndex] += normalizedX < 0 ? -outward : outward;
        vertices[vertexIndex + 1] += lift;
        maxLift = Math.max(maxLift, lift);
        maxOutward = Math.max(maxOutward, outward);
      }
      // Cubism only redraws clipping masks whose vertex-change flag is set.
      // The warp runs after coreModel.update(), so mark the altered mesh here.
      const dynamicFlags = coreModel._model
        && coreModel._model.drawables
        && coreModel._model.drawables.dynamicFlags;
      if (dynamicFlags) dynamicFlags[drawableIndex] |= 32;
    });
    state.lastChestWarpInhale = inhale;
    state.lastChestWarpMaxLift = maxLift;
    state.lastChestWarpMaxOutward = maxOutward;
  }

  function beginSpeechExpression(text) {
    state.lastSpeechText = String(text || "").trim();
    const candidate = chooseSpeechExpression(state.lastSpeechText);
    const now = performance.now();
    if (
      !candidate
      || (
        state.lastSpeechExpressionAt > 0
        && now - state.lastSpeechExpressionAt < SPEECH_EXPRESSION_COOLDOWN_MS
      )
    ) {
      state.speechExpression = null;
      return;
    }
    state.speechExpression = candidate;
    state.lastSpeechExpressionAt = now;
  }

  function updateSpeechExpression(coreModel, elapsed) {
    const target = state.speaking && state.speechExpression ? 1 : 0;
    const smoothing = 1 - Math.pow(target > state.speechExpressionWeight ? 0.82 : 0.88, elapsed);
    state.speechExpressionWeight += (target - state.speechExpressionWeight) * smoothing;
    if (target === 0 && state.speechExpressionWeight < 0.002) {
      state.speechExpressionWeight = 0;
    }

    Object.entries(SPEECH_EXPRESSION_PARAMETERS).forEach(function ([name, parameterId]) {
      if (state.activeExpression === name) return;
      const value = name === state.speechExpression ? state.speechExpressionWeight : 0;
      setCoreParameter(coreModel, parameterId, value);
    });

    if (target === 0 && state.speechExpressionWeight === 0) {
      state.speechExpression = null;
    }
  }

  function updateModelParameters() {
    if (!state.model || !state.model.internalModel) return;
    const now = performance.now();
    const deltaMs = state.lastFrameAt ? clamp(now - state.lastFrameAt, 4, 66.667) : 16.667;
    const elapsed = clamp(deltaMs / 16.667, 0.25, 4);
    state.lastFrameAt = now;
    const coreModel = state.model.internalModel.coreModel;
    const mouthTarget = speechTarget(now);
    const smoothing = 1 - Math.pow(0.58, elapsed);
    state.mouthOpen += (mouthTarget - state.mouthOpen) * smoothing;

    if (state.avatarDirector) {
      try {
        state.avatarDirectorSnapshot = state.avatarDirector.update({
          coreModel: coreModel,
          focusController: state.model.internalModel.focusController,
          nowMs: now,
          deltaMs: deltaMs,
          speaking: state.speaking,
        });
        state.gazeX = Number(state.avatarDirectorSnapshot.eyeX) || 0;
        state.gazeY = Number(state.avatarDirectorSnapshot.eyeY) || 0;
      } catch (error) {
        if (!state.avatarDirectorErrorReported) {
          console.warn("[Evelyn Live2D] avatar director update failed", error);
          state.avatarDirectorErrorReported = true;
        }
      }
    }
    setCoreParameter(coreModel, "ParamMouthOpenY", state.mouthOpen);
    if (state.speaking) {
      setCoreParameter(coreModel, "ParamMouthForm", 0.18);
    }
    updateIdleTail(coreModel, now, elapsed);
    updateSpeechExpression(coreModel, elapsed);
    // Expressions and pose updates run before this hook, so apply the paired toggle last.
    enforceCatAccessoryVisibility(coreModel);

    try {
      state.lastMouthParameter = coreModel.getParameterValueById(resolveCoreParameterId("ParamMouthOpenY"));
      state.lastEyeOpenParameter = coreModel.getParameterValueById(resolveCoreParameterId("ParamEyeLOpen"));
      state.lastBreathParameter = coreModel.getParameterValueById(resolveCoreParameterId("ParamBreath"));
      state.lastCatEarParameter = coreModel.getParameterValueById(resolveCoreParameterId(CAT_EAR_PARAMETER));
      state.lastCatEarPartOpacity = coreModel.getPartOpacityById(resolveCoreParameterId(CAT_EAR_PART));
      state.lastCatTailPartOpacity = coreModel.getPartOpacityById(resolveCoreParameterId(CAT_TAIL_PART));
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
    if (event.code === CAT_ACCESSORY_HOTKEY) {
      event.preventDefault();
      controller.toggleCatAccessories();
      return;
    }
    const expression = HOTKEY_EXPRESSIONS[event.code];
    if (!expression) return;
    event.preventDefault();
    controller.setExpression(expression);
  }

  function bindInteractions() {
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
    restorePersistedModelState();
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
        breathDepth: 0,
      });
      configureNaturalBreathing();
      if (!configureChestMeshWarp()) {
        console.warn("[Evelyn Live2D] chest mesh warp targets unavailable");
      }
      if (window.EvelynAvatarDirector && typeof window.EvelynAvatarDirector.create === "function") {
        state.avatarDirector = window.EvelynAvatarDirector.create({
          resolveParameterId: resolveCoreParameterId,
        });
      } else {
        console.warn("[Evelyn Live2D] avatar director unavailable; continuing without autonomous gaze");
      }
      state.model.anchor.set(0.5, 0.5);
      state.model.internalModel.on("beforeModelUpdate", updateModelParameters);
      state.model.internalModel.on("afterModelUpdate", applyNaturalChestMeshWarp);
      state.app.stage.addChild(state.model);
      state.app.ticker.add(updateLive2DFrame);
      state.ready = true;
      state.area.classList.remove("live2d-error");
      state.area.classList.add("live2d-ready");
      state.area.dataset.live2dCatAccessories = state.catAccessoriesVisible ? "visible" : "hidden";
      enforceCatAccessoryVisibility(state.model.internalModel.coreModel);
      applyActiveExpression();
      fitModel();
      bindInteractions();
      state.resizeObserver = new ResizeObserver(fitModel);
      state.resizeObserver.observe(state.area);
      state.canvas.addEventListener("webglcontextlost", (event) => {
        event.preventDefault();
        markUnavailable(new Error("WebGL context lost"));
      });
      setStatus("Live2D 연결됨 · 상태 기반 자율 시선 · 1–0/F1 표정", "ready");
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
    setSpeaking: function (speaking, speechText) {
      const nextSpeaking = Boolean(speaking);
      const speakingChanged = nextSpeaking !== state.speaking;
      const nextSpeechText = String(speechText || "").trim();
      if (nextSpeaking && (!state.speaking || (nextSpeechText && nextSpeechText !== state.lastSpeechText))) {
        beginSpeechExpression(nextSpeechText);
      }
      state.speaking = nextSpeaking;
      if (
        speakingChanged
        && state.avatarDirector
        && typeof state.avatarDirector.setActivityState === "function"
      ) {
        state.avatarDirector.setActivityState(state.speaking ? "speaking" : "idle");
      }
      if (!state.speaking) state.lastSpeechText = "";
      if (state.area) state.area.classList.toggle("live2d-speaking", state.speaking);
    },
    setActivityState: function (activityState) {
      return state.avatarDirector
        && typeof state.avatarDirector.setActivityState === "function"
        ? state.avatarDirector.setActivityState(activityState)
        : false;
    },
    toggleCatAccessories: function () {
      state.catAccessoriesVisible = !state.catAccessoriesVisible;
      if (state.area) {
        state.area.dataset.live2dCatAccessories = state.catAccessoriesVisible ? "visible" : "hidden";
      }
      const coreModel = state.model && state.model.internalModel && state.model.internalModel.coreModel;
      if (coreModel) enforceCatAccessoryVisibility(coreModel);
      persistModelState();
      return state.catAccessoriesVisible;
    },
    setExpression: function (name) {
      const normalized = String(name || "").trim().toLowerCase();
      if (!Object.prototype.hasOwnProperty.call(EXPRESSION_PARAMETERS, normalized)) return false;
      if (state.activeExpression === normalized) {
        controller.clearExpression();
        return true;
      }
      state.activeExpression = normalized;
      applyActiveExpression();
      persistModelState();
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
      persistModelState();
    },
    availableExpressions: function () {
      return Object.keys(EXPRESSION_PARAMETERS);
    },
    setLookIntent: function (name, options) {
      return state.avatarDirector
        ? state.avatarDirector.setLookIntent(name, options)
        : false;
    },
    clearLookIntent: function () {
      if (state.avatarDirector) state.avatarDirector.clearLookIntent();
    },
    snapshot: function () {
      return {
        ready: state.ready,
        speaking: state.speaking,
        speechExpression: state.speechExpression,
        speechExpressionWeight: Number(state.speechExpressionWeight.toFixed(3)),
        mouthOpen: Number(state.mouthOpen.toFixed(3)),
        mouthParameter: Number(state.lastMouthParameter.toFixed(3)),
        eyeOpenParameter: Number(state.lastEyeOpenParameter.toFixed(3)),
        breathParameter: Number(state.lastBreathParameter.toFixed(3)),
        breathConfigured: state.breathConfigured,
        breathTorsoParameter: 0,
        breathChestParameters: [],
        chestWarp: {
          configured: state.chestWarpReferenceIndex >= 0
            && state.chestWarpDrawableIndices.length > 0,
          drawableCount: state.chestWarpDrawableIndices.length,
          sweaterScale: CHEST_WARP_SWEATER_SCALE,
          globalScale: CHEST_WARP_GLOBAL_SCALE,
          cycleSeconds: NATURAL_BREATH_CYCLE_SECONDS,
          inhale: Number(state.lastChestWarpInhale.toFixed(3)),
          maxLift: Number(state.lastChestWarpMaxLift.toFixed(5)),
          maxOutward: Number(state.lastChestWarpMaxOutward.toFixed(5)),
        },
        catEarParameter: Number(state.lastCatEarParameter.toFixed(3)),
        catEarPartOpacity: Number(state.lastCatEarPartOpacity.toFixed(3)),
        catTailPartOpacity: Number(state.lastCatTailPartOpacity.toFixed(3)),
        catAccessoriesVisible: state.catAccessoriesVisible,
        idleTailWeight: Number(state.idleTailWeight.toFixed(3)),
        tailHeadings: state.idleTailAngles.map(function (angle) {
          return Number(angle.toFixed(3));
        }),
        tailParameters: state.idleTailParameters.map(function (value) {
          return Number(value.toFixed(3));
        }),
        tailRootParameter: Number(state.lastTailRootParameter.toFixed(3)),
        tailTipParameter: Number(state.lastTailTipParameter.toFixed(3)),
        expression: state.activeExpression,
        expressionParameter: state.lastExpressionValue == null
          ? null
          : Number(state.lastExpressionValue.toFixed(3)),
        gaze: {
          x: Number(state.gazeX.toFixed(3)),
          y: Number(state.gazeY.toFixed(3)),
          source: state.avatarDirectorSnapshot && state.avatarDirectorSnapshot.mode
            ? state.avatarDirectorSnapshot.mode
            : "unavailable",
          saccadeCount: state.avatarDirectorSnapshot
            ? state.avatarDirectorSnapshot.saccadeCount
            : 0,
          intent: state.avatarDirectorSnapshot
            ? state.avatarDirectorSnapshot.intent
            : null,
        },
        avatarDirector: state.avatarDirector
          ? state.avatarDirector.snapshot()
          : null,
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
