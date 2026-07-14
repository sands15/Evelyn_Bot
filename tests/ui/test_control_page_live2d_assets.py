from __future__ import annotations

import json
from pathlib import Path
import unittest


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
DOCS_ROOT = REPO_ROOT / "docs"
MODEL_ROOT = DOCS_ROOT / "assets" / "evelyn-avatar" / "live2d"
VENDOR_ROOT = DOCS_ROOT / "assets" / "vendor" / "live2d"


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
            DOCS_ROOT / "assets" / "evelyn-live2d.js",
        ]
        self.assertTrue(all(path.is_file() and path.stat().st_size > 0 for path in expected))

    def test_control_page_loads_live2d_before_controller(self) -> None:
        html = (DOCS_ROOT / "index.html").read_text(encoding="utf-8-sig")
        scripts = [
            "pixi-8.13.1.min.js",
            "live2dcubismcore-5.0.0.min.js",
            "untitled-pixi-live2d-engine-cubism-1.3.1.js",
            "evelyn-live2d.js",
        ]
        positions = [html.index(script) for script in scripts]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('id="evelynLive2dCanvas"', html)
        self.assertIn('typeof voice.speaking === "boolean"', html)
        self.assertIn("Boolean(localBridge.speaking)", html)
        self.assertIn("window.EvelynLive2D.setSpeaking(voiceSpeaking)", html)

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
        self.assertNotIn("autoInteract:", controller)
        self.assertIn('Digit1: "heart eye"', controller)
        self.assertIn('F2: "ulmak"', controller)

    def test_pointer_focus_is_centered_on_the_rendered_head(self) -> None:
        controller = (DOCS_ROOT / "assets" / "evelyn-live2d.js").read_text(encoding="utf-8-sig")
        self.assertIn("const HEAD_FOCUS_X_RATIO = 0.482", controller)
        self.assertIn("const HEAD_FOCUS_Y_RATIO = 0.276", controller)
        self.assertIn("function getModelHeadClientPoint()", controller)
        self.assertIn("state.pointerClientX - head.clientX", controller)
        self.assertIn("head.clientY - state.pointerClientY", controller)
        self.assertIn("Math.hypot(dx, dy)", controller)
        self.assertIn("if (state.pointerActive) updateGazeFromPointer()", controller)
        self.assertNotIn("event.clientX / width", controller)
        self.assertNotIn("event.clientY / height", controller)
        self.assertNotIn("window.innerWidth", controller)
        self.assertNotIn("window.innerHeight", controller)

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
