from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.voice_pipeline_state import (  # noqa: E402
    build_voice_pipeline_snapshot_payload,
    default_voice_pipeline_counters,
    default_voice_pipeline_state,
    increment_voice_counter,
    load_last_voice_channel_state,
    mark_last_voice_manual_disconnect,
    record_voice_failure_state,
    save_last_voice_channel_state,
)


class VoicePipelineStateTests(unittest.TestCase):
    def test_counter_and_failure_state_update_snapshot_fields(self) -> None:
        counters = default_voice_pipeline_counters()
        state = default_voice_pipeline_state()
        state["last_voice_segment_at"] = 90.0
        increment_voice_counter(counters, "queue_full_drop_count", 2)
        error_text = record_voice_failure_state(counters, state, "tts_request_failed", RuntimeError("boom"), now=100.0)

        snapshot = build_voice_pipeline_snapshot_payload(
            counters=counters,
            state=state,
            p95={"stt_ms_p95": 11.2, "tts_first_audio_ms_p95": 22.3, "main_first_token_ms_p95": 33.4},
            now_time=100.0,
            now_mono=50.0,
            stt_lock_locked=True,
            stt_cooldown_until=51.25,
            last_channel_state={"guild_id": 1, "channel_id": 2},
            output_mode="discord_voice",
            local_tts_output={"enabled": True},
            queue_depth=3,
            queue_max=16,
            live_recent_sec=15.0,
            utterance_assembly_enabled=True,
            utterance_pending_count=4,
            utterance_commit_wait_sec=0.22,
            barge_in_continuity={"active": True},
            turn_path_metrics={"voice": {"count": 1}},
        )

        self.assertIn("boom", error_text)
        self.assertEqual(counters["tts_request_failed_count"], 1)
        self.assertEqual(snapshot["queueFullDropCount"], 2)
        self.assertTrue(snapshot["liveRecent"])
        self.assertTrue(snapshot["sttBusy"])
        self.assertEqual(snapshot["sttCooldownRemainingSec"], 1.25)
        self.assertEqual(snapshot["lastVoiceChannel"]["channel_id"], 2)
        self.assertEqual(snapshot["bargeInContinuity"]["active"], True)

    def test_last_voice_channel_state_file_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = default_voice_pipeline_state()
            guild = SimpleNamespace(id=123, name="Guild")
            channel = SimpleNamespace(id=456, name="Voice")

            save_last_voice_channel_state(
                root,
                "state/voice_last_channel.json",
                state,
                guild,
                channel,
                reason="test",
                now=10.0,
            )
            loaded = load_last_voice_channel_state(root, "state/voice_last_channel.json")
            mark_last_voice_manual_disconnect(
                root,
                "state/voice_last_channel.json",
                state,
                guild,
                reason="manual",
                now=11.0,
            )
            marked = load_last_voice_channel_state(root, "state/voice_last_channel.json")

            self.assertEqual(loaded["guild_id"], 123)
            self.assertEqual(loaded["channel_name"], "Voice")
            self.assertTrue(marked["manual_disconnect"])
            self.assertEqual(marked["reason"], "manual")
            self.assertEqual(state["last_voice_channel"]["updated_at"], 11.0)


if __name__ == "__main__":
    unittest.main()
