from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.local_mic_state import (  # noqa: E402
    build_local_mic_runtime_state,
    local_mic_status_line_from_payload,
    normalize_voice_input_mode,
    serialize_local_mic_runtime_state_payload,
    set_voice_input_mode_state,
    voice_input_mode_status_line_from_mode,
)


class LocalMicStateTests(unittest.TestCase):
    def test_normalize_voice_input_mode_aliases(self) -> None:
        self.assertEqual(normalize_voice_input_mode("local-mic"), "local")
        self.assertEqual(normalize_voice_input_mode("vc"), "discord")
        self.assertEqual(normalize_voice_input_mode("unknown"), "auto")

    def test_default_state_and_set_mode(self) -> None:
        state = build_local_mic_runtime_state(
            enabled=True,
            input_mode="mic",
            routed_user_ids={3, 1},
        )
        state["discord_suppression_active"] = True

        self.assertEqual(state["input_mode"], "local")
        self.assertEqual(state["routed_user_ids"], [1, 3])
        self.assertEqual(set_voice_input_mode_state(state, "discord_voice"), "discord")
        self.assertFalse(state["discord_suppression_active"])

    def test_status_line_labels(self) -> None:
        self.assertEqual(voice_input_mode_status_line_from_mode("local"), "local mic only")
        self.assertEqual(voice_input_mode_status_line_from_mode("discord"), "discord voice only")
        self.assertEqual(voice_input_mode_status_line_from_mode("auto"), "auto")

    def test_serialize_runtime_state_with_service_metrics(self) -> None:
        state = build_local_mic_runtime_state(enabled=True, input_mode="auto", routed_user_ids=[42])
        state["last_segment_at"] = 95.0
        state["last_segment_duration_sec"] = 1.25
        state["discord_suppression_active"] = True
        service = SimpleNamespace(
            capture_ready=True,
            last_input_at=98.0,
            input_block_count=7,
            last_input_level=0.1234567,
            max_input_level=0.5,
            last_input_status="voice",
            last_effective_max_silence_ms=350,
            rejected_segment_count=2,
            last_rejected_reason="noise",
            last_segment_filter="accepted",
            sample_rate=22050,
        )

        payload = serialize_local_mic_runtime_state_payload(
            state,
            service=service,
            now=100.0,
            max_silence_ms=500,
            vad_filter_enabled=True,
            env_noise_filter_enabled=False,
            waveform_filter_enabled=True,
            discord_suppress_after_segment_sec=2.0,
            device=None,
            sample_rate=16000,
            start_threshold=0.002,
            continue_threshold=0.001,
        )

        self.assertTrue(payload["captureReady"])
        self.assertEqual(payload["lastSegmentAgeSec"], 5.0)
        self.assertEqual(payload["lastInputAgeSec"], 2.0)
        self.assertEqual(payload["lastInputLevel"], 0.123457)
        self.assertEqual(payload["captureSampleRate"], 22050)
        self.assertEqual(payload["effectiveMaxSilenceMs"], 350)
        self.assertEqual(payload["device"], "default")

    def test_local_mic_status_line_from_payload(self) -> None:
        self.assertEqual(
            local_mic_status_line_from_payload({"enabled": False, "inputModeLabel": "auto"}),
            "auto | disabled",
        )
        self.assertEqual(
            local_mic_status_line_from_payload(
                {
                    "enabled": True,
                    "captureReady": True,
                    "inputModeLabel": "local mic only",
                    "lastSegmentAgeSec": 1.2,
                    "discordSuppressionActive": False,
                }
            ),
            "local mic only | ready | last segment 1.2s ago | discord fallback on",
        )


if __name__ == "__main__":
    unittest.main()
