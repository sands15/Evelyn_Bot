from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
HTML = REPO_ROOT / "docs" / "index.html"
JS = REPO_ROOT / "docs" / "assets" / "evelyn-voice-validation.js"
CSS = REPO_ROOT / "docs" / "assets" / "evelyn-voice-validation.css"
SERVER = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server.py"


class ControlPageVoiceValidationTests(unittest.TestCase):
    def test_mount_and_assets_are_declared(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertIn('id="voiceValidationStartButton"', html)
        self.assertIn('id="voiceValidationMount"', html)
        self.assertIn("evelyn-voice-validation.js", html)
        self.assertIn("evelyn-voice-validation.css", html)

    def test_ui_contains_validation_contract_and_csrf_mutations(self):
        source = JS.read_text(encoding="utf-8")
        self.assertIn("voice-p0.v1", source)
        self.assertIn("/api/control-page/voice-validation/start", source)
        self.assertIn("/api/control-page/voice-validation/confirm", source)
        self.assertIn("/api/control-page/voice-validation/retry", source)
        self.assertIn("attempt: step.attempt", source)
        self.assertIn("/api/control-page/voice-validation/abort", source)
        self.assertIn("/api/control-page/voice-capture-consent/preview", source)
        self.assertIn("/api/control-page/voice-capture-consent/apply", source)
        self.assertIn("/api/control-page/voice-capture-consent/revoke", source)
        self.assertIn("/api/control-page/session", source)
        self.assertIn("X-Evelyn-CSRF-Token", source)
        self.assertIn('eventCount(step, "reply_started") === 1', source)
        self.assertIn('step.kind === "barge_source"', source)
        self.assertIn('const canRetry = step.status === "failed";', source)
        self.assertIn('${canRetry ? "" : "disabled"}>단계 재시도', source)
        self.assertNotIn("rawAudio", source)

    def test_server_registers_all_voice_validation_routes(self):
        source = SERVER.read_text(encoding="utf-8")
        for suffix in ("", "/start", "/confirm", "/retry", "/abort"):
            self.assertIn(f'"/api/control-page/voice-validation{suffix}"', source)
        for suffix in ("", "/preview", "/apply", "/revoke"):
            self.assertIn(
                f'"/api/control-page/voice-capture-consent{suffix}"',
                source,
            )

    def test_ui_exposes_explicit_consent_and_revoke_controls(self):
        source = JS.read_text(encoding="utf-8")
        self.assertIn('data-voice-consent-grant="1"', source)
        self.assertIn('data-voice-consent-revoke="1"', source)
        self.assertIn("최대 ${maxMinutes}분 뒤 자동으로 꺼집니다.", source)
        self.assertIn("원문 음성이나 transcript를 저장하지 않습니다.", source)

    def test_mic_on_panel_command_focuses_validation_controls(self):
        source = HTML.read_text(encoding="utf-8")

        self.assertIn('panel === "voice_validation"', source)
        self.assertIn('getElementById("voiceValidationStartButton")', source)
        self.assertIn("target.scrollIntoView", source)
        self.assertIn("controlPanelCommandGeneration", source)

    def test_javascript_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        result = subprocess.run(
            [node, "--check", str(JS)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
