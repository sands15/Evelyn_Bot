import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const chromePath = process.env.CHROME_PATH
  || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const pageUrl = process.argv[2] || "http://127.0.0.1:4187/?live2dPreview=1";
const debuggingPort = Number(process.env.EVELYN_LIVE2D_CDP_PORT || 19287);
const userDataDir = mkdtempSync(join(tmpdir(), "evelyn-live2d-probe-"));
const chrome = spawn(
  chromePath,
  [
    "--headless=new",
    "--no-first-run",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-features=Translate",
    "--enable-webgl",
    "--ignore-gpu-blocklist",
    `--remote-debugging-port=${debuggingPort}`,
    `--user-data-dir=${userDataDir}`,
    "about:blank",
  ],
  { stdio: "ignore", windowsHide: true }
);

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForJson(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return response.json();
    } catch (error) {
      lastError = error;
    }
    await sleep(100);
  }
  throw lastError || new Error(`Timed out waiting for ${url}`);
}

async function main() {
  const endpoint = `http://127.0.0.1:${debuggingPort}`;
  await waitForJson(`${endpoint}/json/version`, 10000);
  const pageResponse = await fetch(
    `${endpoint}/json/new?${encodeURIComponent(pageUrl)}`,
    { method: "PUT" }
  );
  if (!pageResponse.ok) {
    throw new Error(`Unable to create probe tab: ${pageResponse.status}`);
  }
  const page = await pageResponse.json();
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });

  let sequence = 0;
  const pending = new Map();
  const browserErrors = [];
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(String(event.data));
    if (message.method === "Runtime.exceptionThrown") {
      browserErrors.push(
        message.params?.exceptionDetails?.exception?.description
        || message.params?.exceptionDetails?.text
        || "Runtime.exceptionThrown"
      );
    }
    if (message.method === "Log.entryAdded" && message.params?.entry?.level === "error") {
      browserErrors.push(message.params.entry.text || "Browser log error");
    }
    if (!message.id || !pending.has(message.id)) return;
    const handlers = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) handlers.reject(new Error(JSON.stringify(message.error)));
    else handlers.resolve(message.result);
  });

  function command(method, params = {}) {
    sequence += 1;
    const id = sequence;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async function evaluate(expression, awaitPromise = true) {
    const result = await command("Runtime.evaluate", {
      expression,
      awaitPromise,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || "Runtime evaluation failed");
    }
    return result.result && result.result.value;
  }

  await command("Runtime.enable");
  await command("Page.enable");
  await command("Log.enable");

  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    const ready = await evaluate("Boolean(window.EvelynLive2D && window.EvelynLive2D.isReady())");
    if (ready) break;
    await sleep(150);
  }
  if (!await evaluate("Boolean(window.EvelynLive2D && window.EvelynLive2D.isReady())")) {
    const status = await evaluate(
      "document.getElementById('evelynLive2dStatus')?.textContent || document.title"
    );
    throw new Error(`Live2D did not become ready: ${status}`);
  }

  const breathFramePaths = {
    exhale: process.env.EVELYN_LIVE2D_BREATH_EXHALE_SCREENSHOT || "",
    inhale: process.env.EVELYN_LIVE2D_BREATH_INHALE_SCREENSHOT || "",
  };
  const breathFrameValues = {};
  const breathFrameStates = {};
  let endpointBreathSamples = [];
  if (breathFramePaths.exhale || breathFramePaths.inhale) {
    await evaluate(
      "window.EvelynLive2D.setLookIntent('look-center', { durationMs: 12000, intensity: 1, influence: 'eyes-head' })"
    );
    // Let the model's initial opacity fade and physics settle before comparing
    // inhale/exhale frames.
    await sleep(2000);
    const modelClip = await evaluate(`
      (() => {
        const canvas = document.getElementById('evelynLive2dCanvas');
        const frame = window.EvelynLive2D.snapshot();
        const rect = canvas.getBoundingClientRect();
        const model = frame.model;
        const left = rect.left + model.x - model.width * 0.5;
        const top = rect.top + model.y - model.height * 0.5;
        return {
          x: Math.max(0, left - 12),
          y: Math.max(0, top - 8),
          width: Math.max(1, model.width + 24),
          height: Math.max(1, model.height + 16),
          scale: 3
        };
      })()
    `);
    for (const [phase, outputPath] of Object.entries(breathFramePaths)) {
      if (!outputPath) continue;
      const deadline = Date.now() + 7000;
      const targetReached = phase === "inhale"
        ? (value) => value >= 0.97
        : (value) => value <= 0.03;
      let value = NaN;
      while (Date.now() < deadline) {
        value = Number(await evaluate("window.EvelynLive2D.snapshot().breathParameter"));
        if (targetReached(value)) break;
        await sleep(30);
      }
      if (!targetReached(value)) {
        throw new Error(`Timed out waiting for ${phase} breath frame; last=${value}`);
      }
      const frame = await command("Page.captureScreenshot", {
        format: "png",
        captureBeyondViewport: false,
        fromSurface: true,
        clip: modelClip,
      });
      writeFileSync(outputPath, Buffer.from(frame.data, "base64"));
      breathFrameValues[phase] = value;
      const phaseSnapshot = await evaluate("window.EvelynLive2D.snapshot()");
      breathFrameStates[phase] = {
        torso: phaseSnapshot.breathTorsoParameter,
        chest: phaseSnapshot.breathChestParameters,
        meshWarp: phaseSnapshot.chestWarp,
      };
      if (phase === "inhale") {
        endpointBreathSamples = await evaluate(`
          new Promise((resolve) => {
            const values = [];
            const sample = () => values.push(window.EvelynLive2D.snapshot().breathParameter);
            sample();
            const timer = setInterval(sample, 50);
            setTimeout(() => {
              clearInterval(timer);
              sample();
              resolve(values);
            }, 700);
          })
        `);
      }
    }
    await evaluate("window.EvelynLive2D.clearLookIntent(); true");
  }

  const initial = await evaluate("window.EvelynLive2D.snapshot()");
  const tailSamples = await evaluate(`
    new Promise((resolve) => {
      const values = [];
      const startedAt = performance.now();
      const sample = () => {
        const frame = window.EvelynLive2D.snapshot();
        values.push({
          t: performance.now() - startedAt,
          headings: frame.tailHeadings,
          parameters: frame.tailParameters,
          weight: frame.idleTailWeight,
          speaking: frame.speaking,
        });
      };
      sample();
      const timer = setInterval(sample, 20);
      setTimeout(() => {
        clearInterval(timer);
        sample();
        resolve(values);
      }, 8500);
    })
  `);
  const rootZeroCrossings = tailSamples
    .slice(1)
    .map((sample, index) => ({ previous: tailSamples[index], sample }))
    .filter(({ previous, sample }) => (
      previous.headings[0] < 0
      && sample.headings[0] >= 0
      && sample.headings[0] > previous.headings[0]
    ))
    .map(({ sample }) => sample.t);
  const tailCycleSeconds = rootZeroCrossings.length >= 2
    ? (rootZeroCrossings[1] - rootZeroCrossings[0]) / 1000
    : 0;
  const rootPeakIndex = tailSamples.findIndex((sample, index) => (
    index >= 3
    && index + 3 < tailSamples.length
    && sample.headings[0] - tailSamples[index - 3].headings[0] > 0.03
    && tailSamples[index + 3].headings[0] - sample.headings[0] < -0.03
  ));
  const tailReversalLags = rootPeakIndex >= 0
    ? initial.tailHeadings.map((_heading, segment) => {
      const windowEnd = Math.min(tailSamples.length, rootPeakIndex + 36);
      let peakIndex = rootPeakIndex;
      for (let index = rootPeakIndex + 1; index < windowEnd; index += 1) {
        if (tailSamples[index].headings[segment] > tailSamples[peakIndex].headings[segment]) {
          peakIndex = index;
        }
      }
      return (tailSamples[peakIndex].t - tailSamples[rootPeakIndex].t) / 1000;
    })
    : [];
  const tailParameterErrors = tailSamples.flatMap((sample) => (
    sample.headings.map((heading, segment) => {
      const localHeading = segment === 0 ? heading : heading - sample.headings[segment - 1];
      const gains = [1, 2, 2.5, 3, 3.5, 4, 4];
      const expected = Math.max(-8, Math.min(8, localHeading * gains[segment]));
      return Math.abs(sample.parameters[segment] - expected);
    })
  ));
  const breathSamples = await evaluate(`
    new Promise((resolve) => {
      const values = [];
      const sample = () => values.push(window.EvelynLive2D.snapshot().breathParameter);
      sample();
      const timer = setInterval(sample, 100);
      setTimeout(() => {
        clearInterval(timer);
        sample();
        resolve(values);
      }, 1600);
    })
  `);
  const nextEyeShiftWaitMs = await evaluate(
    "Math.max(150, Math.min(7000, (window.EvelynLive2D.snapshot().avatarDirector?.nextEyeShiftAt || performance.now()) - performance.now() + 180))"
  );
  await sleep(nextEyeShiftWaitMs);
  const autonomous = await evaluate("window.EvelynLive2D.snapshot()");

  await evaluate(
    "window.EvelynLive2D.setSpeaking(true, '오늘 상태를 확인했어.'); true"
  );
  await sleep(250);
  const neutralSpeech = await evaluate("window.EvelynLive2D.snapshot()");
  await evaluate("window.EvelynLive2D.setSpeaking(false, ''); true");

  await evaluate(
    "window.EvelynLive2D.setSpeaking(true, '정말 좋아해.'); true"
  );
  await sleep(250);
  const emotionalSpeech = await evaluate("window.EvelynLive2D.snapshot()");
  await evaluate("window.EvelynLive2D.setSpeaking(false, ''); true");
  await evaluate(
    "window.EvelynLive2D.setSpeaking(true, '지금 너무 슬퍼.'); true"
  );
  await sleep(100);
  const cooldownSpeech = await evaluate("window.EvelynLive2D.snapshot()");
  await evaluate("window.EvelynLive2D.setSpeaking(false, ''); true");

  const targetBeforePointer = await evaluate(
    "window.EvelynLive2D.snapshot().avatarDirector"
  );
  await evaluate(
    "window.dispatchEvent(new PointerEvent('pointermove', { clientX: 1, clientY: 1 })); true"
  );
  await sleep(100);
  const targetAfterPointer = await evaluate(
    "window.EvelynLive2D.snapshot().avatarDirector"
  );

  const headBeforeEyeOnlyIntent = await evaluate(
    "window.EvelynLive2D.snapshot().avatarDirector"
  );
  const eyeOnlyIntentAccepted = await evaluate(
    "window.EvelynLive2D.setLookIntent('look-left', { durationMs: 600, intensity: 0.5, influence: 'eyes' })"
  );
  await sleep(100);
  const eyeOnlyIntent = await evaluate("window.EvelynLive2D.snapshot()");
  await evaluate("window.EvelynLive2D.clearLookIntent(); true");
  const headBeforeCombinedIntent = await evaluate(
    "window.EvelynLive2D.snapshot().avatarDirector"
  );
  const combinedIntentAccepted = await evaluate(
    "window.EvelynLive2D.setLookIntent('look-right', { durationMs: 600, intensity: 0.5, influence: 'eyes-head' })"
  );
  await sleep(100);
  const intent = await evaluate("window.EvelynLive2D.snapshot()");

  const result = {
    ready: initial.ready,
    tailCycleSeconds,
    tailReversalLags,
    tailParameterMaximumError: Math.max(...tailParameterErrors),
    breathConfigured: initial.breathConfigured,
    breathMinimum: Math.min(...breathSamples),
    breathMaximum: Math.max(...breathSamples),
    breathRange: Math.max(...breathSamples) - Math.min(...breathSamples),
    breathFrameValues,
    breathFrameStates,
    endpointPauseRun: (() => {
      const steps = endpointBreathSamples.slice(1).map((value, index) => (
        Math.abs(value - endpointBreathSamples[index])
      ));
      let run = 0;
      let maximumRun = 0;
      for (const step of steps) {
        run = step < 0.003 ? run + 1 : 0;
        maximumRun = Math.max(maximumRun, run);
      }
      return maximumRun;
    })(),
    initialSaccades: initial.avatarDirector?.saccadeCount,
    laterSaccades: autonomous.avatarDirector?.saccadeCount,
    gazeChanged: initial.gaze.x !== autonomous.gaze.x || initial.gaze.y !== autonomous.gaze.y,
    autonomousHeadTargetChangedOnEyeShift: (
      initial.avatarDirector?.headTargetX !== autonomous.avatarDirector?.headTargetX
      || initial.avatarDirector?.headTargetY !== autonomous.avatarDirector?.headTargetY
    ),
    autonomousHeadDecisionWasDue: (
      initial.avatarDirector?.nextHeadDecisionAt
      <= initial.avatarDirector?.nextEyeShiftAt + 180
    ),
    speakingActivityState: neutralSpeech.avatarDirector?.activityState,
    neutralSpeechExpression: neutralSpeech.speechExpression,
    emotionalSpeechExpression: emotionalSpeech.speechExpression,
    cooldownSpeechExpression: cooldownSpeech.speechExpression,
    pointerChangedTarget: (
      targetBeforePointer.targetX !== targetAfterPointer.targetX
      || targetBeforePointer.targetY !== targetAfterPointer.targetY
    ),
    eyeOnlyIntentAccepted,
    eyeOnlyIntentChangedHeadTarget: (
      headBeforeEyeOnlyIntent.headTargetX !== eyeOnlyIntent.avatarDirector?.headTargetX
      || headBeforeEyeOnlyIntent.headTargetY !== eyeOnlyIntent.avatarDirector?.headTargetY
    ),
    combinedIntentAccepted,
    intentMode: intent.avatarDirector?.mode,
    intentName: intent.avatarDirector?.intent,
    intentInfluence: intent.avatarDirector?.intentInfluence,
    combinedIntentChangedHeadTarget: (
      headBeforeCombinedIntent.headTargetX !== intent.avatarDirector?.headTargetX
      || headBeforeCombinedIntent.headTargetY !== intent.avatarDirector?.headTargetY
    ),
    combinedIntentHeadStep: Math.hypot(
      intent.avatarDirector?.headX - headBeforeCombinedIntent.headX,
      intent.avatarDirector?.headY - headBeforeCombinedIntent.headY
    ),
    directorError: intent.avatarDirector?.lastError,
    browserErrors,
    canvas: intent.canvas,
  };

  if (!result.ready) throw new Error("Live2D snapshot is not ready");
  if (!(result.tailCycleSeconds > 3.4 && result.tailCycleSeconds < 4.1)) {
    throw new Error(`Idle tail cycle is outside the reference range: ${JSON.stringify(result)}`);
  }
  if (
    tailSamples.some((sample) => sample.speaking || sample.weight < 0.99)
    || !(result.tailParameterMaximumError < 0.02)
  ) {
    throw new Error(`Idle tail parameters did not reach the Cubism model: ${JSON.stringify(result)}`);
  }
  if (
    result.tailReversalLags.length !== 7
    || result.tailReversalLags.some((lag, index) => (
      index > 0 && lag < result.tailReversalLags[index - 1]
    ))
    || !(result.tailReversalLags[6] > 0.3 && result.tailReversalLags[6] < 0.5)
  ) {
    throw new Error(`Idle tail reversal did not propagate root-to-tip: ${JSON.stringify(result)}`);
  }
  if (!result.breathConfigured || !(result.breathRange > 0.1)) {
    throw new Error(`Natural chest breathing did not advance: ${JSON.stringify(result)}`);
  }
  if (endpointBreathSamples.length && result.endpointPauseRun > 1) {
    throw new Error(`Breath paused near an endpoint: ${JSON.stringify(result)}`);
  }
  if (!(result.laterSaccades > result.initialSaccades)) {
    throw new Error(`Autonomous saccade did not advance: ${JSON.stringify(result)}`);
  }
  if (!result.gazeChanged) throw new Error(`Autonomous gaze did not move: ${JSON.stringify(result)}`);
  if (!result.autonomousHeadDecisionWasDue && result.autonomousHeadTargetChangedOnEyeShift) {
    throw new Error(`Autonomous eye shift also changed the head target: ${JSON.stringify(result)}`);
  }
  if (result.speakingActivityState !== "speaking") {
    throw new Error(`Speaking state did not reach Avatar Director: ${JSON.stringify(result)}`);
  }
  if (result.neutralSpeechExpression !== null) {
    throw new Error(`Neutral speech forced an expression: ${JSON.stringify(result)}`);
  }
  if (result.emotionalSpeechExpression !== "heart eye") {
    throw new Error(`Strong emotion did not select an expression: ${JSON.stringify(result)}`);
  }
  if (result.cooldownSpeechExpression !== null) {
    throw new Error(`Expression cooldown was not enforced: ${JSON.stringify(result)}`);
  }
  if (result.pointerChangedTarget) {
    throw new Error(`Pointer movement changed autonomous target: ${JSON.stringify(result)}`);
  }
  if (!result.eyeOnlyIntentAccepted || result.eyeOnlyIntentChangedHeadTarget) {
    throw new Error(`Eye-only intent changed the head target: ${JSON.stringify(result)}`);
  }
  if (
    !result.combinedIntentAccepted
    || result.intentMode !== "intent"
    || result.intentName !== "look-right"
    || result.intentInfluence !== "eyes-head"
    || !result.combinedIntentChangedHeadTarget
  ) {
    throw new Error(`Look intent was not applied: ${JSON.stringify(result)}`);
  }
  if (!(result.combinedIntentHeadStep < 0.04)) {
    throw new Error(`Head intent transition was too abrupt: ${JSON.stringify(result)}`);
  }
  if (result.directorError) throw new Error(`Avatar Director error: ${result.directorError}`);
  if (result.browserErrors.length) {
    throw new Error(`Browser runtime errors: ${JSON.stringify(result.browserErrors)}`);
  }

  if (process.env.EVELYN_LIVE2D_SCREENSHOT) {
    const screenshot = await command("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: false,
      fromSurface: true,
    });
    writeFileSync(
      process.env.EVELYN_LIVE2D_SCREENSHOT,
      Buffer.from(screenshot.data, "base64")
    );
  }

  console.log(JSON.stringify(result, null, 2));
  socket.close();
}

try {
  await main();
} finally {
  if (!chrome.killed) chrome.kill();
}
