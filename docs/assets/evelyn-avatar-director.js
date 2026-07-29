(function () {
  "use strict";

  /*
   * State-aware eye contact, probabilistic head participation, and animation
   * timing are adapted from TalkingHead:
   * https://github.com/met4citizen/TalkingHead
   * Revision reviewed: eed58d198076a7e1e825f804802921c4d3804d46
   *
   * MIT License
   * Copyright (c) 2023-2024 Mika Suominen
   *
   * Permission is hereby granted, free of charge, to any person obtaining a copy
   * of this software and associated documentation files (the "Software"), to deal
   * in the Software without restriction, including without limitation the rights
   * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
   * copies of the Software, and to permit persons to whom the Software is
   * furnished to do so, subject to the following conditions:
   *
   * The above copyright notice and this permission notice shall be included in all
   * copies or substantial portions of the Software.
   *
   * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
   * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
   * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
   * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
   * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
   * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
   * SOFTWARE.
   *
   * The EYES / HEAD / EYES_HEAD influence vocabulary is adapted from Performs:
   * https://github.com/upf-gti/performs
   * Revision reviewed: ea38464ac24a7914925fbda666df6b1838dda672
   *
   * MIT License
   * Copyright (c) 2026 UPF-GTI
   *
   * Permission is hereby granted, free of charge, to any person obtaining a copy
   * of this software and associated documentation files (the "Software"), to deal
   * in the Software without restriction, including without limitation the rights
   * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
   * copies of the Software, and to permit persons to whom the Software is
   * furnished to do so, subject to the following conditions:
   *
   * The above copyright notice and this permission notice shall be included in all
   * copies or substantial portions of the Software.
   *
   * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
   * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
   * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
   * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
   * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
   * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
   * SOFTWARE.
   */

  const BEHAVIOR_PROFILES = Object.freeze({
    idle: Object.freeze({
      eyeContactProbability: 0.20,
      headMoveProbability: 0.50,
      eyeIntervalMs: Object.freeze([2200, 6500]),
      eyeRange: Object.freeze([0.55, 0.32]),
      headRange: Object.freeze([0.16, 0.10]),
      headTimeConstantMs: 1500,
    }),
    listening: Object.freeze({
      eyeContactProbability: 0.50,
      headMoveProbability: 0.50,
      eyeIntervalMs: Object.freeze([3000, 7500]),
      eyeRange: Object.freeze([0.42, 0.24]),
      headRange: Object.freeze([0.12, 0.08]),
      headTimeConstantMs: 1700,
    }),
    speaking: Object.freeze({
      eyeContactProbability: 0.50,
      headMoveProbability: 0.50,
      eyeIntervalMs: Object.freeze([2600, 7000]),
      eyeRange: Object.freeze([0.46, 0.26]),
      headRange: Object.freeze([0.20, 0.12]),
      headTimeConstantMs: 1150,
    }),
    thinking: Object.freeze({
      eyeContactProbability: 0.15,
      headMoveProbability: 0.50,
      eyeIntervalMs: Object.freeze([2400, 6000]),
      eyeRange: Object.freeze([0.60, 0.34]),
      headRange: Object.freeze([0.22, 0.13]),
      headTimeConstantMs: 1350,
    }),
  });

  const LOOK_INTENTS = Object.freeze({
    left: Object.freeze([-0.82, -0.04]),
    right: Object.freeze([0.82, -0.04]),
    up: Object.freeze([0, 0.58]),
    down: Object.freeze([0, -0.62]),
    center: Object.freeze([0, 0]),
    "look-left": Object.freeze([-0.82, -0.04]),
    "look-right": Object.freeze([0.82, -0.04]),
    "look-up": Object.freeze([0, 0.58]),
    "look-down": Object.freeze([0, -0.62]),
    "look-center": Object.freeze([0, 0]),
  });

  const VALID_ACTIVITY_STATES = Object.freeze(Object.keys(BEHAVIOR_PROFILES));

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function lerp(from, to, amount) {
    return from + (to - from) * amount;
  }

  function nowMs() {
    return typeof performance !== "undefined" && typeof performance.now === "function"
      ? performance.now()
      : Date.now();
  }

  function randomBetween(random, range) {
    return range[0] + random() * (range[1] - range[0]);
  }

  function centerWeightedRandom(random) {
    return ((random() + random() + random()) - 1.5) / 1.5;
  }

  function normalizeActivityState(value) {
    const normalized = String(value || "").trim().toLowerCase();
    return Object.prototype.hasOwnProperty.call(BEHAVIOR_PROFILES, normalized)
      ? normalized
      : null;
  }

  function normalizeInfluence(value) {
    const normalized = String(value || "eyes-head")
      .trim()
      .toLowerCase()
      .replace(/_/g, "-");
    if (normalized === "eyes" || normalized === "eye") return "eyes";
    if (normalized === "head" || normalized === "head-only") return "head";
    if (
      normalized === "eyes-head"
      || normalized === "head-eyes"
      || normalized === "neck"
      || normalized === "both"
    ) {
      return "eyes-head";
    }
    return "eyes-head";
  }

  function includesEyes(influence) {
    return influence === "eyes" || influence === "eyes-head";
  }

  function includesHead(influence) {
    return influence === "head" || influence === "eyes-head";
  }

  function create(options) {
    const settings = options && typeof options === "object" ? options : {};
    const random = typeof settings.random === "function" ? settings.random : Math.random;
    const resolveParameterId = typeof settings.resolveParameterId === "function"
      ? settings.resolveParameterId
      : function (id) { return id; };
    const state = {
      enabled: settings.enabled !== false,
      activityState: normalizeActivityState(settings.activityState) || "idle",
      eyeTargetX: 0,
      eyeTargetY: 0,
      eyeX: 0,
      eyeY: 0,
      eyeContact: true,
      nextEyeShiftAt: 0,
      lastEyeShiftAt: 0,
      saccadeCount: 0,
      headTargetX: 0,
      headTargetY: 0,
      headX: 0,
      headY: 0,
      nextHeadDecisionAt: 0,
      lastHeadMoveAt: 0,
      headMoveCount: 0,
      headDecisionReason: "center",
      blinkPhase: "idle",
      blinkProgress: 0,
      blinkDelayMs: 3000 + random() * 5000,
      blinkOpenDurationMs: 220,
      eyeOpen: 1,
      intent: null,
      intentExpiresAt: 0,
      lastUpdateAt: 0,
      lastError: "",
    };

    function profile() {
      return BEHAVIOR_PROFILES[state.activityState] || BEHAVIOR_PROFILES.idle;
    }

    function setParameter(coreModel, id, value) {
      try {
        coreModel.setParameterValueById(resolveParameterId(id), value);
      } catch (_error) {
        // Optional mirrored parameters differ between model revisions.
      }
    }

    function scheduleEyeShift(now) {
      const currentProfile = profile();
      state.eyeContact = random() < currentProfile.eyeContactProbability;
      if (state.eyeContact) {
        state.eyeTargetX = centerWeightedRandom(random) * 0.035;
        state.eyeTargetY = centerWeightedRandom(random) * 0.025;
      } else {
        state.eyeTargetX = centerWeightedRandom(random) * currentProfile.eyeRange[0];
        state.eyeTargetY = centerWeightedRandom(random) * currentProfile.eyeRange[1] - 0.025;
        if (Math.abs(state.eyeTargetX) < 0.08 && Math.abs(state.eyeTargetY) < 0.06) {
          state.eyeTargetX += random() < 0.5 ? -0.10 : 0.10;
        }
      }
      state.lastEyeShiftAt = now;
      state.nextEyeShiftAt = now + randomBetween(random, currentProfile.eyeIntervalMs);
      state.saccadeCount += 1;

      // TalkingHead evaluates HeadMove for every eye animation. A value of 0.5
      // means that roughly half of autonomous eye shifts also receive a slow
      // head gesture; it is not a separate low-frequency head timer.
      if (random() < currentProfile.headMoveProbability) {
        if (state.eyeContact) {
          state.headTargetX = centerWeightedRandom(random) * currentProfile.headRange[0] * 0.62;
          state.headTargetY = centerWeightedRandom(random) * currentProfile.headRange[1] * 0.62;
          state.headDecisionReason = "eye-contact-participation";
        } else {
          state.headTargetX = clamp(
            state.eyeTargetX * 0.38,
            -currentProfile.headRange[0],
            currentProfile.headRange[0]
          );
          state.headTargetY = clamp(
            state.eyeTargetY * 0.34,
            -currentProfile.headRange[1],
            currentProfile.headRange[1]
          );
          state.headDecisionReason = "gaze-participation";
        }
        state.lastHeadMoveAt = now;
        state.headMoveCount += 1;
      } else {
        state.headTargetX = 0;
        state.headTargetY = 0;
        state.headDecisionReason = "eyes-only";
      }
      state.nextHeadDecisionAt = state.nextEyeShiftAt;
    }

    function updateBlink(deltaMs) {
      const safeDelta = clamp(Number(deltaMs) || 16.667, 1, 100);
      if (state.blinkPhase === "idle") {
        state.blinkDelayMs = Math.max(0, state.blinkDelayMs - safeDelta);
        if (state.blinkDelayMs === 0) {
          state.blinkPhase = "closing";
          state.blinkProgress = 0;
        }
        state.eyeOpen = 1;
        return;
      }

      if (state.blinkPhase === "closing") {
        state.blinkProgress = Math.min(1, state.blinkProgress + safeDelta / 75);
        const eased = 1 - Math.pow(1 - state.blinkProgress, 2);
        state.eyeOpen = clamp(1 - eased, 0, 1);
        if (state.blinkProgress >= 1) {
          state.blinkPhase = "opening";
          state.blinkProgress = 0;
          state.blinkOpenDurationMs = 150 + random() * 150;
        }
        return;
      }

      state.blinkProgress = Math.min(
        1,
        state.blinkProgress + safeDelta / state.blinkOpenDurationMs
      );
      state.eyeOpen = clamp(state.blinkProgress * state.blinkProgress, 0, 1);
      if (state.blinkProgress >= 1) {
        state.blinkPhase = "idle";
        state.blinkProgress = 0;
        state.blinkDelayMs = 3000 + random() * 5000;
        state.eyeOpen = 1;
      }
    }

    function expireIntent(now) {
      state.intent = null;
      state.intentExpiresAt = 0;
      state.nextEyeShiftAt = now + 700 + random() * 800;
      state.nextHeadDecisionAt = state.nextEyeShiftAt;
    }

    function applyIntentTarget(now) {
      if (!state.intent) return false;
      if (now >= state.intentExpiresAt) {
        expireIntent(now);
        return false;
      }
      if (includesEyes(state.intent.influence)) {
        state.eyeTargetX = state.intent.eyeX;
        state.eyeTargetY = state.intent.eyeY;
      }
      if (includesHead(state.intent.influence)) {
        state.headTargetX = state.intent.headX;
        state.headTargetY = state.intent.headY;
        state.headDecisionReason = "intent";
      }
      return true;
    }

    function setActivityState(value) {
      const normalized = normalizeActivityState(value);
      if (!normalized) return false;
      if (normalized === state.activityState) return true;
      state.activityState = normalized;
      const now = nowMs();
      state.nextEyeShiftAt = Math.min(state.nextEyeShiftAt || now, now + 900);
      state.nextHeadDecisionAt = state.nextEyeShiftAt;
      return true;
    }

    function update(input) {
      if (!state.enabled) return snapshot();
      const frame = input && typeof input === "object" ? input : {};
      const coreModel = frame.coreModel;
      const focusController = frame.focusController;
      const now = Number.isFinite(frame.nowMs) ? frame.nowMs : nowMs();
      const deltaMs = Number.isFinite(frame.deltaMs)
        ? frame.deltaMs
        : (state.lastUpdateAt ? now - state.lastUpdateAt : 16.667);
      state.lastUpdateAt = now;
      if (frame.activityState) {
        setActivityState(frame.activityState);
      } else if (frame.speaking === true) {
        setActivityState("speaking");
      } else if (frame.speaking === false && state.activityState === "speaking") {
        setActivityState("idle");
      }
      if (!coreModel) return snapshot();

      try {
        const intentActive = applyIntentTarget(now);
        if (!intentActive) {
          if (state.nextEyeShiftAt === 0 || now >= state.nextEyeShiftAt) {
            scheduleEyeShift(now);
          }
        }

        const safeDeltaMs = clamp(Number(deltaMs) || 16.667, 1, 100);
        const eyeSmoothing = 1 - Math.exp(-safeDeltaMs / 75);
        const headSmoothing = 1 - Math.exp(
          -safeDeltaMs / profile().headTimeConstantMs
        );
        state.eyeX = lerp(state.eyeX, state.eyeTargetX, eyeSmoothing);
        state.eyeY = lerp(state.eyeY, state.eyeTargetY, eyeSmoothing);
        state.headX = lerp(state.headX, state.headTargetX, headSmoothing);
        state.headY = lerp(state.headY, state.headTargetY, headSmoothing);

        if (focusController && typeof focusController.focus === "function") {
          focusController.focus(state.headX, state.headY, false);
        }

        updateBlink(safeDeltaMs);
        setParameter(coreModel, "ParamEyeBallX", state.eyeX);
        setParameter(coreModel, "ParamEyeBallY", state.eyeY);
        setParameter(coreModel, "ParamEyeBallX2", state.eyeX);
        setParameter(coreModel, "ParamEyeBallY2", state.eyeY);
        setParameter(coreModel, "ParamEyeLOpen", state.eyeOpen);
        setParameter(coreModel, "ParamEyeROpen", state.eyeOpen);
        state.lastError = "";
      } catch (error) {
        state.lastError = error && error.message ? error.message : String(error);
      }
      return snapshot();
    }

    function setLookIntent(name, optionsValue) {
      const normalized = String(name || "").trim().toLowerCase().replace(/_/g, "-");
      const target = LOOK_INTENTS[normalized];
      if (!target) return false;
      const intentOptions = optionsValue && typeof optionsValue === "object" ? optionsValue : {};
      const requestedIntensity = Number(intentOptions.intensity);
      const intensity = clamp(Number.isFinite(requestedIntensity) ? requestedIntensity : 1, 0, 1);
      const requestedDurationMs = Number(intentOptions.durationMs);
      const durationMs = clamp(
        Number.isFinite(requestedDurationMs) ? requestedDurationMs : 1800,
        120,
        12000
      );
      const influence = normalizeInfluence(intentOptions.influence);
      const now = Number.isFinite(intentOptions.nowMs) ? intentOptions.nowMs : nowMs();
      state.intent = {
        name: normalized,
        influence: influence,
        eyeX: target[0] * intensity,
        eyeY: target[1] * intensity,
        headX: target[0] * intensity * 0.30,
        headY: target[1] * intensity * 0.24,
      };
      state.intentExpiresAt = now + durationMs;
      applyIntentTarget(now);
      return true;
    }

    function clearLookIntent() {
      if (!state.intent) return;
      expireIntent(nowMs());
    }

    function setEnabled(enabled) {
      state.enabled = Boolean(enabled);
      if (!state.enabled) {
        state.eyeTargetX = 0;
        state.eyeTargetY = 0;
        state.eyeX = 0;
        state.eyeY = 0;
        state.headTargetX = 0;
        state.headTargetY = 0;
        state.headX = 0;
        state.headY = 0;
        state.intent = null;
        state.intentExpiresAt = 0;
      }
      return state.enabled;
    }

    function snapshot() {
      return {
        enabled: state.enabled,
        mode: state.intent ? "intent" : "autonomous",
        activityState: state.activityState,
        targetX: state.eyeTargetX,
        targetY: state.eyeTargetY,
        eyeTargetX: state.eyeTargetX,
        eyeTargetY: state.eyeTargetY,
        eyeX: state.eyeX,
        eyeY: state.eyeY,
        eyeContact: state.eyeContact,
        headTargetX: state.headTargetX,
        headTargetY: state.headTargetY,
        headX: state.headX,
        headY: state.headY,
        headDecisionReason: state.headDecisionReason,
        headMoveCount: state.headMoveCount,
        eyeOpen: state.eyeOpen,
        blinkPhase: state.blinkPhase,
        nextSaccadeAt: state.nextEyeShiftAt,
        nextEyeShiftAt: state.nextEyeShiftAt,
        nextHeadDecisionAt: state.nextHeadDecisionAt,
        saccadeCount: state.saccadeCount,
        intent: state.intent ? state.intent.name : null,
        intentInfluence: state.intent ? state.intent.influence : null,
        intentExpiresAt: state.intentExpiresAt,
        lastError: state.lastError,
      };
    }

    return {
      update: update,
      setActivityState: setActivityState,
      setLookIntent: setLookIntent,
      clearLookIntent: clearLookIntent,
      setEnabled: setEnabled,
      snapshot: snapshot,
    };
  }

  window.EvelynAvatarDirector = Object.freeze({
    create: create,
    behaviorProfiles: BEHAVIOR_PROFILES,
    validActivityStates: VALID_ACTIVITY_STATES,
    randomSaccadeInterval: function (random) {
      const source = typeof random === "function" ? random : Math.random;
      return randomBetween(source, BEHAVIOR_PROFILES.idle.eyeIntervalMs);
    },
  });
})();
