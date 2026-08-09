from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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
    record_voice_pipeline_failure_from_runtime,
    save_last_voice_channel_state,
    save_last_voice_channel_state_from_runtime,
)
import evelyn_core.voice_pipeline_state as voice_pipeline_state_module  # noqa: E402


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

        self.assertEqual(error_text, "tts_request_failed")
        self.assertEqual(counters["tts_request_failed_count"], 1)
        self.assertEqual(snapshot["queueFullDropCount"], 2)
        self.assertTrue(snapshot["liveRecent"])
        self.assertTrue(snapshot["sttBusy"])
        self.assertEqual(snapshot["sttCooldownRemainingSec"], 1.25)
        self.assertEqual(snapshot["lastVoiceChannel"]["channel_id"], 2)
        self.assertEqual(snapshot["bargeInContinuity"]["active"], True)

    def test_record_voice_pipeline_failure_updates_state_and_logs_event(self) -> None:
        counters = default_voice_pipeline_counters()
        state = default_voice_pipeline_state()
        metrics = {
            "meta": {
                "turn_id": "turn-1",
                "segment_id": 2,
                "chunk_index": 3,
                "session_key": "session-1",
                "room_session_key": "room-1",
                "guild_id": 7,
                "source": "voice",
            }
        }
        events: list[tuple[str, dict]] = []

        record_voice_pipeline_failure_from_runtime(
            counters,
            state,
            "tts_request_failed",
            RuntimeError("boom"),
            merge_log_event_payload=lambda *, explicit, extra=None: {**explicit, **(extra or {})},
            log_turn_event=lambda event, **payload: events.append((event, payload)),
            metrics=metrics,
            stage="tts",
        )

        self.assertEqual(counters["tts_request_failed_count"], 1)
        self.assertEqual(state["last_failure"]["kind"], "tts_request_failed")
        self.assertEqual(state["last_failure"]["errorType"], "RuntimeError")
        self.assertTrue(state["last_failure"]["contentFree"])
        self.assertNotIn("error", state["last_failure"])
        self.assertEqual(events[0][0], "tts_request_failed")
        self.assertEqual(events[0][1]["turn_id"], "turn-1")
        self.assertEqual(events[0][1]["room_session_key"], "room-1")
        self.assertEqual(events[0][1]["stage"], "tts")
        self.assertEqual(events[0][1]["error"], "tts_request_failed")
        self.assertEqual(events[0][1]["error_type"], "RuntimeError")
        self.assertNotIn("boom", str(events))

    def test_playback_failure_marks_typed_turn_summary_evidence(self) -> None:
        metrics = {"meta": {"turn_id": "turn-playback"}}

        record_voice_pipeline_failure_from_runtime(
            default_voice_pipeline_counters(),
            default_voice_pipeline_state(),
            "tts_playback_failed",
            RuntimeError("private playback detail"),
            merge_log_event_payload=lambda *, explicit, extra=None: {
                **explicit,
                **(extra or {}),
            },
            log_turn_event=lambda _event, **_payload: None,
            metrics=metrics,
        )

        self.assertIs(metrics["meta"]["playback_failed"], True)
        self.assertNotIn("private playback detail", str(metrics))

    def test_snapshot_closes_over_legacy_private_failure_fields(self) -> None:
        state = default_voice_pipeline_state()
        state.update(
            {
                "last_failure": {
                    "kind": "tts_request_failed",
                    "error": "Bearer private-token C:\\private",
                    "errorType": "Bearer private-token C:\\private",
                    "at": "Bearer private-token C:\\private",
                },
                "last_voice_rejoin_error": "C:\\private\\token",
                "last_voice_rejoin_error_type": (
                    "Bearer private-token C:\\private"
                ),
            }
        )

        snapshot = build_voice_pipeline_snapshot_payload(
            counters=default_voice_pipeline_counters(),
            state=state,
            p95={},
            now_time=100.0,
            now_mono=50.0,
            stt_lock_locked=False,
            stt_cooldown_until=0.0,
            last_channel_state=None,
            output_mode="discord_voice",
            local_tts_output={},
            queue_depth=0,
            queue_max=16,
            live_recent_sec=15.0,
            utterance_assembly_enabled=False,
            utterance_pending_count=0,
            utterance_commit_wait_sec=0.0,
            barge_in_continuity={},
            turn_path_metrics={},
        )

        self.assertEqual(
            snapshot["lastFailure"],
            {
                "kind": "tts_request_failed",
                "errorType": "",
                "at": None,
                "contentFree": True,
            },
        )
        self.assertEqual(
            snapshot["lastVoiceRejoinError"],
            "voice_rearm_failed",
        )
        self.assertEqual(snapshot["lastVoiceRejoinErrorType"], "")
        serialized = str(snapshot)
        self.assertNotIn("private-token", serialized)
        self.assertNotIn("C:\\private", serialized)

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

    def test_save_last_voice_channel_state_from_runtime_reports_failures(self) -> None:
        state = default_voice_pipeline_state()
        logs: list[str] = []
        private_error = "PRIVATE_VOICE_STATE_SAVE C:/secret/voice-channel.json"

        with patch.object(
            voice_pipeline_state_module,
            "save_last_voice_channel_state",
            side_effect=RuntimeError(private_error),
        ):
            ok = save_last_voice_channel_state_from_runtime(
                Path("root"),
                "state/voice_last_channel.json",
                state,
                SimpleNamespace(id=1, name="Guild"),
                SimpleNamespace(id=2, name="Voice"),
                reason="test",
                log=logs.append,
            )

        self.assertFalse(ok)
        self.assertEqual(
            logs,
            ["[VOICE STATE SAVE FAIL] errorType=RuntimeError"],
        )
        self.assertNotIn(private_error, repr(logs))


if __name__ == "__main__":
    unittest.main()
