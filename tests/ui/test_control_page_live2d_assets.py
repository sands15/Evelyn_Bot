from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
DOCS_ROOT = REPO_ROOT / "docs"
MODEL_ROOT = DOCS_ROOT / "assets" / "evelyn-avatar" / "live2d"
VENDOR_ROOT = DOCS_ROOT / "assets" / "vendor" / "live2d"
DIRECTOR_PATH = DOCS_ROOT / "assets" / "evelyn-avatar-director.js"


class ControlPageLive2DAssetTests(unittest.TestCase):
    def test_model_manifest_references_exist(self) -> None:
        manifest_path = MODEL_ROOT / "evelin.model3.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        references = manifest["FileReferences"]
        paths = [references["Moc"], references["Physics"], references["DisplayInfo"]]
        paths.extend(references["Textures"])
        paths.extend(item["File"] for item in references["Expressions"])
        missing = [path for path in paths if not (MODEL_ROOT / path).is_file()]
        self.assertEqual(missing, [])
        self.assertEqual(len(references["Textures"]), 6)
        self.assertEqual(len(references["Expressions"]), 12)

    def test_local_runtime_and_controller_are_present(self) -> None:
        expected = [
            VENDOR_ROOT / "pixi-8.13.1.min.js",
            VENDOR_ROOT / "live2dcubismcore-5.0.0.min.js",
            VENDOR_ROOT / "untitled-pixi-live2d-engine-cubism-1.3.1.js",
            DIRECTOR_PATH,
            DOCS_ROOT / "assets" / "evelyn-live2d.js",
        ]
        self.assertTrue(all(path.is_file() and path.stat().st_size > 0 for path in expected))

    def test_control_page_loads_live2d_before_controller(self) -> None:
        html = (DOCS_ROOT / "index.html").read_text(encoding="utf-8-sig")
        scripts = [
            "pixi-8.13.1.min.js",
            "live2dcubismcore-5.0.0.min.js",
            "untitled-pixi-live2d-engine-cubism-1.3.1.js",
            "evelyn-avatar-director.js",
            "evelyn-live2d.js",
        ]
        positions = [html.index(script) for script in scripts]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('id="evelynLive2dCanvas"', html)
        self.assertIn('typeof voice.speaking === "boolean"', html)
        self.assertIn("Boolean(localBridge.speaking)", html)
        self.assertIn("function latestAssistantChatText(messages)", html)
        self.assertIn("window.EvelynLive2D.setSpeaking(voiceSpeaking, latestAssistantChatText(chatMessages))", html)

    def test_legacy_model_fallback_and_assets_are_removed(self) -> None:
        html = (DOCS_ROOT / "index.html").read_text(encoding="utf-8-sig")
        audio_preview = (DOCS_ROOT / "evelyn-audio-device-preview.html").read_text(encoding="utf-8-sig")
        legacy_root = DOCS_ROOT / "assets" / "evelyn-avatar"

        self.assertNotIn("evelynLive2dFallback", html)
        self.assertNotIn('class="live2d-model"', html)
        self.assertNotIn("model-v2", html)
        self.assertNotIn("model-v2", audio_preview)
        self.assertFalse((DOCS_ROOT / "assets" / "evelyn-page.js").exists())
        self.assertFalse((legacy_root / "model-v2").exists())
        self.assertFalse((legacy_root / "parts").exists())

    def test_live2d_preview_mode_does_not_poll_the_control_api(self) -> None:
        html = (DOCS_ROOT / "index.html").read_text(encoding="utf-8-sig")
        self.assertIn('get("live2dPreview") === "1"', html)
        self.assertIn("if (LIVE2D_PREVIEW_MODE)", html)
        self.assertIn('setBootProgress(100, "Live2D 미리보기 준비 완료", { hide: true })', html)
        self.assertIn("composer.disabled = true", html)

    def test_controller_drives_model_on_the_pixi_ticker(self) -> None:
        controller = (DOCS_ROOT / "assets" / "evelyn-live2d.js").read_text(encoding="utf-8-sig")
        self.assertIn("state.app.ticker.maxFPS = 60", controller)
        self.assertIn("state.app.ticker.add(updateLive2DFrame)", controller)
        self.assertIn("autoHitTest: false", controller)
        self.assertIn("autoFocus: false", controller)
        self.assertIn("breathDepth: 0", controller)
        self.assertNotIn("autoInteract:", controller)
        self.assertIn('Digit1: "heart eye"', controller)
        self.assertIn('F2: "ulmak"', controller)

    def test_default_figure_eight_head_breath_is_disabled(self) -> None:
        controller = (DOCS_ROOT / "assets" / "evelyn-live2d.js").read_text(encoding="utf-8-sig")
        self.assertIn("function configureNaturalBreathing()", controller)
        self.assertIn("configureNaturalBreathing();", controller)
        self.assertIn("parameter.offset = 0.5", controller)
        self.assertIn("parameter.peak = 0.5", controller)
        self.assertIn("parameter.cycle = NATURAL_BREATH_CYCLE_SECONDS", controller)
        self.assertIn("parameter.weight = 1", controller)
        self.assertIn('parameter.waveform = "triangle"', controller)
        self.assertIn("breathParameter", controller)
        self.assertIn("breathConfigured", controller)
        self.assertNotIn('setCoreParameter(coreModel, "ParamBreath"', controller)
        self.assertNotIn('"ParamAngleX"', controller)
        self.assertNotIn('"ParamAngleY"', controller)
        self.assertNotIn('"ParamAngleZ"', controller)
        self.assertNotIn("VISIBLE_BREATH_BUST_Y", controller)
        self.assertNotIn("applyVisibleChestBreathing", controller)
        self.assertNotIn("VISIBLE_BREATH_VERTICAL_STRETCH", controller)
        self.assertNotIn("updateVisibleBreathingTransform", controller)
        self.assertNotIn('const NATURAL_BREATH_TORSO_PARAMETER = "ParamBodyAngleX2"', controller)
        self.assertNotIn("NATURAL_BREATH_TORSO_LIFT", controller)
        self.assertNotIn("applyNaturalChestBreathing", controller)
        self.assertIn("breathTorsoParameter", controller)
        self.assertNotIn("NATURAL_BREATH_CHEST_EXPANSION", controller)
        self.assertIn("const NATURAL_BREATH_CYCLE_SECONDS = 9.6", controller)
        self.assertIn('const CHEST_WARP_REFERENCE_DRAWABLE = "Bra"', controller)
        self.assertIn("const CHEST_WARP_DRAWABLE_IDS", controller)
        self.assertIn("const CHEST_WARP_SWEATER_DRAWABLE_IDS", controller)
        self.assertIn("const CHEST_WARP_SWEATER_SCALE = 0.7", controller)
        self.assertIn("const CHEST_WARP_GLOBAL_SCALE = 1", controller)
        self.assertIn("* CHEST_WARP_GLOBAL_SCALE", controller)
        self.assertNotIn('"Top1",', controller)
        self.assertNotIn('"Top2",', controller)
        self.assertNotIn('"ArtMesh129",', controller)
        self.assertIn("function configureChestMeshWarp()", controller)
        self.assertIn("function applyNaturalChestMeshWarp()", controller)
        self.assertIn("const inhale = breathValue", controller)
        self.assertIn('state.model.internalModel.on("afterModelUpdate", applyNaturalChestMeshWarp)', controller)
        self.assertIn("vertices[vertexIndex + 1] += lift", controller)
        self.assertIn("* drawableScale", controller)
        self.assertIn("normalizedX < 0 ? -outward : outward", controller)
        self.assertIn("dynamicFlags[drawableIndex] |= 32", controller)
        self.assertIn("breathChestParameters", controller)
        engine = (
            DOCS_ROOT
            / "assets"
            / "vendor"
            / "live2d"
            / "untitled-pixi-live2d-engine-cubism-1.3.1.js"
        ).read_text(encoding="utf-8-sig")
        self.assertIn('this.emit("afterModelUpdate");', engine)
        self.assertIn('data.waveform === "triangle"', engine)
        self.assertIn("2 / Math.PI * Math.asin(sineValue)", engine)
        self.assertNotIn("setParameterProbe", controller)

    def test_param_breath_drives_bust_physics(self) -> None:
        physics = json.loads(
            (MODEL_ROOT / "evelin.physics3.json").read_text(encoding="utf-8-sig")
        )
        settings = {item["Id"]: item for item in physics["PhysicsSettings"]}
        for setting_id in ("PhysicsSetting10", "PhysicsSetting11"):
            setting = settings[setting_id]
            inputs = [item["Source"]["Id"] for item in setting["Input"]]
            outputs = [item["Destination"]["Id"] for item in setting["Output"]]
            self.assertIn("ParamBreath", inputs)
            self.assertGreaterEqual(len(outputs), 3)

    def test_avatar_director_separates_autonomous_eye_and_head_focus(self) -> None:
        controller = (DOCS_ROOT / "assets" / "evelyn-live2d.js").read_text(encoding="utf-8-sig")
        director = DIRECTOR_PATH.read_text(encoding="utf-8-sig")

        self.assertIn("window.EvelynAvatarDirector.create", controller)
        self.assertIn("state.avatarDirector.update({", controller)
        self.assertIn("focusController: state.model.internalModel.focusController", controller)
        self.assertIn("setLookIntent: function (name, options)", controller)
        self.assertNotIn('addEventListener("pointermove"', controller)
        self.assertNotIn("pointerClientX", controller)
        self.assertNotIn("pointerActive", controller)
        self.assertIn("const BEHAVIOR_PROFILES", director)
        self.assertIn("eyeContactProbability", director)
        self.assertIn("headMoveProbability", director)
        self.assertGreaterEqual(director.count("headMoveProbability: 0.50"), 4)
        self.assertIn("eyeContactProbability: 0.20", director)
        self.assertIn("function scheduleEyeShift(now)", director)
        self.assertNotIn("function scheduleHeadDecision(now)", director)
        self.assertIn("state.nextHeadDecisionAt = state.nextEyeShiftAt", director)
        self.assertIn('state.headDecisionReason = "gaze-participation"', director)
        self.assertIn('state.headDecisionReason = "eyes-only"', director)
        self.assertIn("focusController.focus(state.headX, state.headY, false)", director)
        self.assertNotIn("focusController.focus(state.targetX * 0.5", director)
        self.assertIn('setParameter(coreModel, "ParamEyeBallX2", state.eyeX)', director)
        self.assertIn('setParameter(coreModel, "ParamEyeBallY2", state.eyeY)', director)
        self.assertIn("Copyright (c) 2023-2024 Mika Suominen", director)
        self.assertIn("Copyright (c) 2026 UPF-GTI", director)
        self.assertIn("setActivityState: setActivityState", director)
        self.assertIn("setActivityState: function (activityState)", controller)
        self.assertIn("const speakingChanged = nextSpeaking !== state.speaking", controller)
        self.assertIn("speakingChanged", controller)

    def test_digit_seven_toggles_cat_ears_and_tail_together(self) -> None:
        controller = (DOCS_ROOT / "assets" / "evelyn-live2d.js").read_text(encoding="utf-8-sig")
        self.assertIn('const CAT_EAR_PARAMETER = "ParamHairBack65"', controller)
        self.assertIn('const CAT_EAR_PART = "ear"', controller)
        self.assertIn('const CAT_TAIL_PART = "Part2"', controller)
        self.assertIn('const CAT_ACCESSORY_HOTKEY = "Digit7"', controller)
        self.assertNotIn('Digit7: "cat ear"', controller)
        self.assertIn("function enforceCatAccessoryVisibility(coreModel)", controller)
        self.assertIn("const opacity = state.catAccessoriesVisible ? 1 : 0", controller)
        self.assertIn("setCoreParameter(coreModel, CAT_EAR_PARAMETER, opacity)", controller)
        self.assertIn("coreModel.setPartOpacityById(resolveCoreParameterId(CAT_EAR_PART), opacity)", controller)
        self.assertIn("coreModel.setPartOpacityById(resolveCoreParameterId(CAT_TAIL_PART), opacity)", controller)
        self.assertIn("controller.toggleCatAccessories()", controller)
        self.assertIn("toggleCatAccessories: function ()", controller)
        self.assertIn("enforceCatAccessoryVisibility(coreModel)", controller)
        self.assertIn("catEarParameter", controller)
        self.assertIn("catEarPartOpacity", controller)
        self.assertIn("catTailPartOpacity", controller)
        self.assertIn("catAccessoriesVisible", controller)

    def test_user_selected_model_state_persists_across_page_reloads(self) -> None:
        controller = (DOCS_ROOT / "assets" / "evelyn-live2d.js").read_text(encoding="utf-8-sig")
        self.assertIn('const MODEL_STATE_STORAGE_KEY = "evelynLive2dModelStateV1"', controller)
        self.assertIn("function restorePersistedModelState()", controller)
        self.assertIn("function persistModelState()", controller)
        self.assertIn("window.localStorage.getItem(MODEL_STATE_STORAGE_KEY)", controller)
        self.assertIn("window.localStorage.setItem(MODEL_STATE_STORAGE_KEY", controller)
        self.assertIn("activeExpression: state.activeExpression", controller)
        self.assertIn("catAccessoriesVisible: state.catAccessoriesVisible", controller)
        self.assertIn("restorePersistedModelState();", controller)
        self.assertIn("function applyActiveExpression()", controller)
        self.assertGreaterEqual(controller.count("persistModelState();"), 3)

    def test_speech_expressions_are_sparse_and_neutral_replies_keep_neutral_face(self) -> None:
        controller = (DOCS_ROOT / "assets" / "evelyn-live2d.js").read_text(encoding="utf-8-sig")
        self.assertIn("const SPEECH_EXPRESSION_PARAMETERS", controller)
        self.assertIn("const SPEECH_EXPRESSION_COOLDOWN_MS = 9000", controller)
        self.assertIn("function chooseSpeechExpression(text)", controller)
        self.assertIn("function beginSpeechExpression(text)", controller)
        self.assertIn("function updateSpeechExpression(coreModel, elapsed)", controller)
        self.assertIn("updateSpeechExpression(coreModel, elapsed)", controller)
        self.assertIn("setSpeaking: function (speaking, speechText)", controller)
        self.assertIn("state.activeExpression === name", controller)
        self.assertIn("speechExpression: state.speechExpression", controller)
        self.assertIn("speechExpressionWeight", controller)
        self.assertIn("return null;", controller)
        self.assertNotIn('return state.speechExpressionSequence % 2 === 0 ? "cheek1" : "cheek2"', controller)
        self.assertNotIn("speechExpressionSequence", controller)

    def test_avatar_director_runtime_updates_focus_and_accepts_temporary_intent(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        script = f"""
global.window = {{}};
require({json.dumps(str(DIRECTOR_PATH))});
const values = [0.2, 0.8, 0.6, 0.4, 0.3, 0.7, 0.5, 0.1];
let cursor = 0;
const random = () => values[(cursor++) % values.length];
const parameters = {{}};
const focusCalls = [];
const director = window.EvelynAvatarDirector.create({{ random }});
const coreModel = {{
  setParameterValueById(id, value) {{ parameters[id] = value; }}
}};
const focusController = {{
  focus(x, y, instant) {{ focusCalls.push([x, y, instant]); }}
}};
let snapshot = director.update({{ coreModel, focusController, nowMs: 1000, deltaMs: 16.667 }});
if (snapshot.mode !== "autonomous" || snapshot.saccadeCount !== 1) process.exit(11);
if (!focusCalls.length || !Number.isFinite(parameters.ParamEyeBallX)) process.exit(12);
const initialHeadDecisionAt = snapshot.nextHeadDecisionAt;
const initialHeadTargetX = snapshot.headTargetX;
snapshot = director.update({{ coreModel, focusController, nowMs: snapshot.nextEyeShiftAt + 1, deltaMs: 16.667 }});
if (snapshot.saccadeCount !== 2) process.exit(16);
if (snapshot.nextHeadDecisionAt === initialHeadDecisionAt) process.exit(17);
if (snapshot.nextHeadDecisionAt !== snapshot.nextEyeShiftAt) process.exit(18);
if (!director.setActivityState("listening")) process.exit(19);
snapshot = director.snapshot();
if (snapshot.activityState !== "listening") process.exit(20);
if (!director.setLookIntent("look-left", {{ nowMs: 1100, durationMs: 500, intensity: 0.5, influence: "eyes" }})) process.exit(13);
snapshot = director.update({{ coreModel, focusController, nowMs: 1200, deltaMs: 16.667 }});
if (snapshot.mode !== "intent" || snapshot.intent !== "look-left" || snapshot.intentInfluence !== "eyes") process.exit(14);
if (snapshot.headTargetX !== initialHeadTargetX) process.exit(21);
snapshot = director.update({{ coreModel, focusController, nowMs: 1700, deltaMs: 16.667 }});
if (snapshot.mode !== "autonomous" || snapshot.intent !== null) process.exit(15);
console.log(JSON.stringify(snapshot));
"""
        result = subprocess.run(
            [node, "-e", script],
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_avatar_director_long_run_keeps_head_motion_sparse_and_slow(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        script = f"""
global.window = {{}};
require({json.dumps(str(DIRECTOR_PATH))});
let seed = 0x5eed1234;
const random = () => {{
  seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
  return seed / 4294967296;
}};
const director = window.EvelynAvatarDirector.create({{ random }});
const coreModel = {{ setParameterValueById() {{}} }};
const focusController = {{ focus() {{}} }};
let previous = director.snapshot();
let maxHeadStep = 0;
let maxHeadTarget = 0;
for (let now = 0; now <= 60000; now += 16.667) {{
  if (now >= 20000 && previous.activityState === "idle") director.setActivityState("speaking");
  if (now >= 40000 && previous.activityState === "speaking") director.setActivityState("listening");
  const next = director.update({{ coreModel, focusController, nowMs: now, deltaMs: 16.667 }});
  maxHeadStep = Math.max(
    maxHeadStep,
    Math.hypot(next.headX - previous.headX, next.headY - previous.headY)
  );
  maxHeadTarget = Math.max(
    maxHeadTarget,
    Math.abs(next.headTargetX),
    Math.abs(next.headTargetY)
  );
  previous = next;
}}
if (previous.activityState !== "listening") process.exit(21);
if (previous.saccadeCount < 8) process.exit(22);
const participation = previous.headMoveCount / previous.saccadeCount;
if (!(participation >= 0.35 && participation <= 0.65)) process.exit(23);
if (!(maxHeadStep < 0.01)) process.exit(24);
if (!(maxHeadTarget <= 0.22)) process.exit(25);
if (previous.lastError) process.exit(26);
console.log(JSON.stringify({{
  eyeShifts: previous.saccadeCount,
  headMoves: previous.headMoveCount,
  maxHeadStep,
  maxHeadTarget,
}}));
"""
        result = subprocess.run(
            [node, "-e", script],
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_idle_tail_uses_all_seven_tail_rotation_parameters(self) -> None:
        controller = (DOCS_ROOT / "assets" / "evelyn-live2d.js").read_text(encoding="utf-8-sig")
        expected = [
            "Param_Angle_Rotation15",
            "Param_Angle_Rotation9",
            "Param_Angle_Rotation10",
            "Param_Angle_Rotation11",
            "Param_Angle_Rotation12",
            "Param_Angle_Rotation13",
            "Param_Angle_Rotation14",
        ]
        self.assertTrue(all(parameter in controller for parameter in expected))
        self.assertIn("const targetWeight = state.speaking ? 0 : 1", controller)
        self.assertIn("updateIdleTail(coreModel, now, elapsed)", controller)
        self.assertIn("idleTailAngles", controller)
        self.assertIn("idleTailVelocities", controller)
        self.assertIn("const rootTarget", controller)
        self.assertIn("const springForce", controller)
        self.assertIn("Math.exp(-parameter.damping * frameSeconds)", controller)
        self.assertIn("previousAngle * parameter.follow + travelingBend", controller)
        self.assertNotIn("tipFlick", controller)
        self.assertRegex(controller, r'Rotation15", follow: 0\.00')
        self.assertRegex(controller, r'Rotation14", follow: 0\.78')
        self.assertIn("tailTipParameter", controller)

    def test_pixi_8_texture_binding_compatibility_patch_is_present(self) -> None:
        engine = (
            VENDOR_ROOT / "untitled-pixi-live2d-engine-cubism-1.3.1.js"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("renderer.texture.bind(texture, 0)", engine)
        self.assertNotIn("texture.source._gpuData[renderer.uid]", engine)


if __name__ == "__main__":
    unittest.main()
