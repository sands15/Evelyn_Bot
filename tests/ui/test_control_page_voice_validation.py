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

    def test_ui_starts_local_only_and_hides_consent_for_terminal_session(self):
        source = JS.read_text(encoding="utf-8")

        self.assertIn('surfaces: new Set(["local"])', source)
        self.assertNotIn('surfaces: new Set(["local", "discord"])', source)
        self.assertIn(
            'action.consent && ["passed", "failed", "aborted"].includes(validationState)',
            source,
        )

    def test_ui_serializes_mutations_and_ignores_stale_poll_results(self):
        source = JS.read_text(encoding="utf-8")
        refresh = source.split("async function refresh()", 1)[1].split(
            "function scheduleRefresh", 1
        )[0]
        exclusive = source.split("function beginExclusive()", 1)[1].split(
            "async function startValidation", 1
        )[0]

        self.assertIn("revision: 0", source)
        self.assertIn("const revision = ++state.revision;", refresh)
        self.assertGreaterEqual(
            refresh.count("if (revision !== state.revision) return;"),
            2,
        )
        self.assertIn("if (state.busy) return null;", exclusive)
        self.assertIn("const revision = ++state.revision;", exclusive)
        self.assertIn("endExclusive(revision);", exclusive)
        self.assertGreaterEqual(exclusive.count("beginExclusive();"), 1)
        self.assertIn('mount.querySelectorAll("button, input")', source)
        self.assertIn("if (state.busy) return;", source)
        for function_name in (
            "repair",
            "grantVoiceCaptureConsent",
            "revokeVoiceCaptureConsent",
        ):
            function = source.split(f"async function {function_name}", 1)[1].split(
                "\n  }", 1
            )[0]
            self.assertIn("beginExclusive();", function)

    def test_heard_confirmation_matches_backend_playback_preconditions(self):
        source = JS.read_text(encoding="utf-8")
        can_confirm = source.split("const canConfirm =", 1)[1].split(
            "const canRetry =", 1
        )[0]

        self.assertIn('step.status !== "failed"', can_confirm)
        self.assertIn("step.heard !== true", can_confirm)
        self.assertIn(
            'eventCount(step, "playback_started") === 1',
            can_confirm,
        )
        self.assertIn(
            'eventCount(step, "playback_completed") === 1',
            can_confirm,
        )

    def test_page_preserves_public_voice_conflict_codes(self):
        source = HTML.read_text(encoding="utf-8")

        for code in (
            "voice_capture_validation_context_not_allowed",
            "voice_capture_confirm_token_stale",
            "validation_attempt_revision_mismatch",
            "validation_step_failed",
            "playback_not_completed",
        ):
            self.assertIn(f'"{code}"', source)
        self.assertIn('evelynErrorCode.startsWith("validation_")', source)

    def test_mic_on_panel_command_focuses_validation_controls(self):
        source = HTML.read_text(encoding="utf-8")

        self.assertIn('panel === "voice_validation"', source)
        self.assertIn('drawer.classList.add("open")', source)
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
