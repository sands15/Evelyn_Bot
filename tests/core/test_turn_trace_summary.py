import sys
import unittest
from pathlib import Path
import json
import tempfile
import threading


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.turn_trace import TURN_SUMMARY_KEYS, build_turn_summary_payload, write_turn_trace_event  # noqa: E402


class TurnTraceSummaryTests(unittest.TestCase):
    def test_summary_keys_include_pipeline_extraction_contract(self) -> None:
        required = {
            "turn_id",
            "route",
            "needs_main_llm",
            "needs_memory",
            "needs_tts",
            "playback_started",
            "playback_completed",
            "playback_cancelled",
            "tts_first_audio_ms",
            "playback_first_packet_ms",
            "memory_context_state",
            "memory_grounding_state",
            "memory_use_policy",
            "memory_confirm_only_item_count",
            "memory_prompt_truncated",
            "memory_prompt_evidence_discarded",
            "memory_pretruncation_legacy_item_count",
            "memory_pretruncation_note_count",
            "memory_opaque_confirm_only_component_count",
            "memory_supplied_note_ids",
            "memory_legacy_evidence_ids",
            "memory_legacy_source_evidence_ids",
            "memory_legacy_source_turn_ids",
            "memory_legacy_confirm_only_item_count",
            "memory_receipt_content_free",
            "memory_writer_decision",
            "memory_write_state",
            "memory_write_note_id",
            "memory_write_error",
            "memory_write_content_free",
            "minecraft_snapshot_freshness",
            "error_layer",
            "validation_session_id",
            "validation_step_id",
            "validation_transcript_match",
        }

        self.assertTrue(required.issubset(set(TURN_SUMMARY_KEYS)))

    def test_summary_payload_keeps_stable_keys_and_nulls(self) -> None:
        payload = build_turn_summary_payload(
            {
                "started_at": 10.0,
                "marks": {
                    "t_main_done": 123.456,
                    "t_tts_first_audio": 88.8,
                },
                "meta": {
                    "turn_id": "turn-1",
                    "source": "voice",
                    "context_pipeline": {
                        "route": "main_direct",
                        "policy": {
                            "needs_main_llm": True,
                            "needs_memory": False,
                            "needs_runtime_state": True,
                            "needs_search": False,
                            "needs_tts": True,
                            "priority": "latency",
                            "response_mode": "short",
                        },
                        "message_count": 4,
                        "sections": ["runtime"],
                        "section_chars": {"runtime": 120},
                        "memory_receipt": {
                            "state": "provided",
                            "groundingState": "partial",
                            "usePolicy": "memory.context-use.v1",
                            "confirmOnlyItemCount": 1,
                            "promptTruncated": False,
                            "promptEvidenceDiscarded": False,
                            "preTruncationLegacyItemCount": 0,
                            "preTruncationNoteCount": 0,
                            "opaqueConfirmOnlyComponentCount": 0,
                            "suppliedNoteIds": ["note-2", "note-1", "note-2"],
                            "suppliedNoteCount": 2,
                            "legacyItemCount": 3,
                            "legacyAttributedItemCount": 2,
                            "legacyUnattributedItemCount": 1,
                            "legacyConfirmOnlyItemCount": 1,
                            "legacyEvidenceIds": ["turn:a:user", "turn:a:assistant"],
                            "legacySourceEvidenceIds": ["turn:source:user"],
                            "legacySourceTurnIds": ["a"],
                            "hotContextState": "provided",
                            "memoryVersion": 7,
                            "contentFree": True,
                        },
                    },
                    "minecraft_snapshot_age_ms": 1234.4,
                    "memory_write_receipt": {
                        "schema": "memory.user-confirmation.v1",
                        "state": "stored",
                        "noteId": "concept-confirmed",
                        "sourceRef": "turn:turn-12345:user",
                        "confirmedAt": "2026-07-31T00:00:00+00:00",
                        "contentFree": True,
                    },
                    "minecraft_snapshot_freshness": "fresh",
                    "validation_session_id": "validation-1",
                    "validation_step_id": "02-listening",
                    "validation_transcript_match": True,
                },
            },
            label="voice_turn",
            event_name="voice_turn_summary",
            total_ms=456.789,
            p95_summary={"stt_ms_p95": 11, "search_followup_queued_count": 2},
        )

        self.assertEqual(tuple(payload.keys()), TURN_SUMMARY_KEYS)
        self.assertEqual(payload["summary_schema"], "turn_summary.v1")
        self.assertEqual(payload["turn_id"], "turn-1")
        self.assertEqual(payload["route"], "main_direct")
        self.assertEqual(payload["needs_main_llm"], True)
        self.assertEqual(payload["needs_memory"], False)
        self.assertEqual(payload["needs_search"], False)
        self.assertEqual(payload["needs_tts"], True)
        self.assertEqual(payload["route_priority"], "latency")
        self.assertEqual(payload["response_mode"], "short")
        self.assertEqual(payload["memory_context_state"], "provided")
        self.assertEqual(payload["memory_grounding_state"], "partial")
        self.assertEqual(payload["memory_use_policy"], "memory.context-use.v1")
        self.assertEqual(payload["memory_confirm_only_item_count"], 1)
        self.assertFalse(payload["memory_prompt_truncated"])
        self.assertFalse(payload["memory_prompt_evidence_discarded"])
        self.assertEqual(payload["memory_pretruncation_legacy_item_count"], 0)
        self.assertEqual(payload["memory_pretruncation_note_count"], 0)
        self.assertEqual(payload["memory_opaque_confirm_only_component_count"], 0)
        self.assertEqual(payload["memory_supplied_note_ids"], ["note-2", "note-1"])
        self.assertEqual(payload["memory_supplied_note_count"], 2)
        self.assertEqual(payload["memory_legacy_item_count"], 3)
        self.assertEqual(payload["memory_legacy_attributed_item_count"], 2)
        self.assertEqual(payload["memory_legacy_unattributed_item_count"], 1)
        self.assertEqual(payload["memory_legacy_confirm_only_item_count"], 1)
        self.assertEqual(
            payload["memory_legacy_evidence_ids"],
            ["turn:a:user", "turn:a:assistant"],
        )
        self.assertEqual(
            payload["memory_legacy_source_evidence_ids"],
            ["turn:source:user"],
        )
        self.assertEqual(payload["memory_legacy_source_turn_ids"], ["a"])
        self.assertEqual(payload["memory_hot_context_state"], "provided")
        self.assertEqual(payload["memory_version"], 7)
        self.assertTrue(payload["memory_receipt_content_free"])
        self.assertEqual(payload["memory_write_state"], "stored")
        self.assertEqual(
            payload["memory_write_note_id"],
            "concept-confirmed",
        )
        self.assertIsNone(payload["memory_write_error"])
        self.assertTrue(payload["memory_write_content_free"])
        self.assertNotIn(
            "sourceRef",
            json.dumps(payload, ensure_ascii=False),
        )
        self.assertEqual(payload["minecraft_snapshot_age_ms"], 1234.4)
        self.assertEqual(payload["minecraft_snapshot_freshness"], "fresh")
        self.assertEqual(payload["validation_session_id"], "validation-1")
        self.assertEqual(payload["validation_step_id"], "02-listening")
        self.assertTrue(payload["validation_transcript_match"])
        self.assertEqual(payload["context_tokens_estimate"], 30)
        self.assertEqual(payload["llm_ms"], 123.5)
        self.assertEqual(payload["tts_first_audio_ms"], 88.8)
        self.assertIsNone(payload["playback_completed"])
        self.assertIsNone(payload["error"])

    def test_error_is_serialized_without_raising(self) -> None:
        payload = build_turn_summary_payload(
            {"meta": {"turn_id": "turn-err"}, "marks": {}},
            label="text_turn",
            event_name="text_turn_summary",
            total_ms=None,
            error_layer="text_turn",
            error=ValueError("bad"),
        )

        self.assertEqual(payload["error_layer"], "text_turn")
        self.assertEqual(payload["error"], "ValueError")
        self.assertIsNone(payload["total_ms"])

    def test_error_message_is_replaced_with_content_free_code(self) -> None:
        payload = build_turn_summary_payload(
            {
                "meta": {
                    "turn_id": "turn-private",
                    "error": "Bearer private-token C:\\private\\voice.wav",
                },
                "marks": {},
            },
            label="voice_turn",
            event_name="voice_turn_summary",
            total_ms=1.0,
            error_layer="voice_delivery",
        )

        self.assertEqual(payload["error"], "turn_failed")
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("private-token", serialized)
        self.assertNotIn("voice.wav", serialized)

    def test_voice_failure_code_remains_actionable(self) -> None:
        payload = build_turn_summary_payload(
            {"meta": {"error": "tts_request_failed"}, "marks": {}},
            label="voice_turn",
            event_name="voice_turn_summary",
            total_ms=1.0,
            error_layer="tts",
        )

        self.assertEqual(payload["error"], "tts_request_failed")

    def test_invalid_memory_write_receipt_fails_closed_without_private_fields(self) -> None:
        payload = build_turn_summary_payload(
            {
                "meta": {
                    "turn_id": "turn-private",
                    "memory_write_receipt": {
                        "schema": "memory.user-confirmation.v1",
                        "state": "stored",
                        "noteId": "concept-safe-id",
                        "sourceRef": "turn:turn-private:user",
                        "confirmedAt": "2026-07-31T00:00:00+00:00",
                        "contentFree": True,
                        "body": "private-memory-body",
                    },
                }
            },
            label="voice_turn",
            event_name="voice_turn_summary",
            total_ms=1.0,
        )

        self.assertIsNone(payload["memory_write_state"])
        self.assertIsNone(payload["memory_write_note_id"])
        self.assertIsNone(payload["memory_write_error"])
        self.assertIsNone(payload["memory_write_content_free"])
        self.assertNotIn(
            "private-memory-body",
            json.dumps(payload, ensure_ascii=False),
        )

    def test_playback_completed_is_explicit_in_summary(self) -> None:
        payload = build_turn_summary_payload(
            {
                "marks": {"t_playback_first_packet": 42.0},
                "meta": {
                    "turn_id": "turn-playback",
                    "playback_cancelled": False,
                },
            },
            label="voice_turn",
            event_name="voice_turn_summary",
            total_ms=100.0,
        )

        self.assertEqual(payload["playback_started"], True)
        self.assertEqual(payload["playback_completed"], True)
        self.assertEqual(payload["playback_cancelled"], False)

    def test_explicit_playback_completed_false_is_preserved(self) -> None:
        payload = build_turn_summary_payload(
            {
                "marks": {"t_playback_first_packet": 42.0},
                "meta": {
                    "turn_id": "turn-playback",
                    "playback_completed": False,
                    "playback_cancelled": True,
                },
            },
            label="voice_turn",
            event_name="voice_turn_summary",
            total_ms=100.0,
        )

        self.assertEqual(payload["playback_started"], True)
        self.assertEqual(payload["playback_completed"], False)
        self.assertEqual(payload["playback_cancelled"], True)

    def test_writer_filters_events_and_writes_jsonl(self) -> None:
        printed: list[str] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            skipped = write_turn_trace_event(
                "ignored",
                {"turn_id": "turn-skip"},
                turn_trace_json_log=True,
                bottleneck_events={"model_call"},
                console_only_stt_and_reply=True,
                voice_bottleneck_logs=True,
                voice_trace_all_events=False,
                log_dir=Path(tmpdir),
                file_lock=threading.Lock(),
                original_print=printed.append,
                trace_print=printed.append,
            )
            written = write_turn_trace_event(
                "model_call",
                {"turn_id": "turn-1", "empty": None},
                turn_trace_json_log=True,
                bottleneck_events={"model_call"},
                console_only_stt_and_reply=True,
                voice_bottleneck_logs=True,
                voice_trace_all_events=False,
                log_dir=Path(tmpdir),
                file_lock=threading.Lock(),
                original_print=printed.append,
                trace_print=printed.append,
            )
            files = list(Path(tmpdir).glob("*.jsonl"))
            record = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])

        self.assertIsNone(skipped)
        self.assertIsNotNone(written)
        self.assertEqual(len(files), 1)
        self.assertEqual(record["event"], "model_call")
        self.assertEqual(record["turn_id"], "turn-1")
        self.assertNotIn("empty", record)
        self.assertTrue(any("[TURN TRACE]" in item for item in printed))

    def test_writer_preserves_summary_null_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            record = write_turn_trace_event(
                "voice_turn_summary",
                {"turn_id": "turn-1", "playback_completed": None},
                turn_trace_json_log=True,
                bottleneck_events={"voice_turn_summary"},
                console_only_stt_and_reply=True,
                voice_bottleneck_logs=True,
                voice_trace_all_events=False,
                log_dir=Path(tmpdir),
                file_lock=threading.Lock(),
                original_print=lambda _text: None,
                trace_print=lambda _text: None,
            )

        self.assertIsNotNone(record)
        self.assertIn("playback_completed", record)
        self.assertIsNone(record["playback_completed"])


if __name__ == "__main__":
    unittest.main()
