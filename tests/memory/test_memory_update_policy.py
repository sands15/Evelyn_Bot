from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.memory_update_policy import (  # noqa: E402
    MemoryRefreshInputs,
    build_memory_writer_decision_for_turn,
    build_memory_writer_decision_payload,
    build_memory_turn_rows,
    memory_refresh_inputs_for_turn,
    memory_scope_labels,
    plan_memory_writebehind_schedule,
    redact_vision_text_for_memory,
    should_run_memory_update,
    write_memory_turn_records,
)
from evelyn_core.memory_update_runtime import schedule_memory_update_from_runtime  # noqa: E402


class MemoryUpdatePolicyTests(unittest.TestCase):
    def test_memory_turn_rows_and_scope_labels_keep_write_contract(self) -> None:
        rows = build_memory_turn_rows(
            user_text=" 안녕 ",
            answer=" 응 ",
            source="voice",
            user_speaker="정훈",
            assistant_speaker="이블린",
        )

        self.assertEqual(
            rows,
            [
                {"role": "user", "speaker": "정훈", "source": "voice", "text": "안녕"},
                {"role": "assistant", "speaker": "이블린", "source": "voice", "text": "응"},
            ],
        )
        self.assertEqual(
            memory_scope_labels(room_key="r", person_key="p", session_memory_key="s"),
            ["guild", "room:r", "person:p", "session:s"],
        )

    def test_memory_turn_rows_add_content_free_turn_evidence_when_available(self) -> None:
        rows = build_memory_turn_rows(
            user_text="private user text",
            answer="private assistant text",
            source="voice",
            turn_id="abc123def456",
        )

        self.assertEqual(rows[0]["evidence_id"], "turn:abc123def456:user")
        self.assertEqual(rows[1]["evidence_id"], "turn:abc123def456:assistant")
        self.assertTrue(all(row["source_turn_id"] == "abc123def456" for row in rows))
        self.assertTrue(all(row["evidence_kind"] == "conversation_turn" for row in rows))
        evidence = str(
            [
                {
                    key: row[key]
                    for key in ("evidence_id", "source_turn_id", "evidence_kind")
                }
                for row in rows
            ]
        )
        self.assertNotIn("private", evidence)

    def test_explicit_fact_or_question_runs_memory_update(self) -> None:
        self.assertTrue(
            should_run_memory_update(
                user_text="나는 아침에 작업하기로 했어",
                answer="기억할게",
                source="text",
            )
        )
        self.assertTrue(
            should_run_memory_update(
                user_text="다음 단계가 뭐야?",
                answer="정리해볼게",
                source="text",
            )
        )

    def test_smalltalk_and_short_voice_are_skipped_unless_periodic_turn(self) -> None:
        self.assertFalse(
            should_run_memory_update(
                user_text="안녕",
                answer="응",
                source="text",
                turn_index=1,
                idle_gap_sec=30,
            )
        )
        self.assertFalse(
            should_run_memory_update(
                user_text="응",
                answer="계속 말해줘",
                source="voice",
                turn_index=1,
                idle_gap_sec=30,
                deep_routing_needed=True,
            )
        )
        self.assertTrue(
            should_run_memory_update(
                user_text="응",
                answer="계속 말해줘",
                source="voice",
                turn_index=4,
            )
        )

    def test_non_smalltalk_runs_after_idle_gap(self) -> None:
        self.assertFalse(
            should_run_memory_update(
                user_text="조금 긴 작업 상태를 정리하고 있어",
                answer="좋아, 이어서 볼게",
                source="text",
                idle_gap_sec=19.9,
            )
        )
        self.assertTrue(
            should_run_memory_update(
                user_text="조금 긴 작업 상태를 정리하고 있어",
                answer="좋아, 이어서 볼게",
                source="text",
                idle_gap_sec=20.0,
            )
        )

    def test_vision_text_is_redacted_unless_enabled(self) -> None:
        text = (
            "Local screen vision observation is available.\n"
            "captured_image=C:/tmp/screen.png\n"
            "ocr_text: secret text\n"
            "scene: desktop"
        )

        redacted = redact_vision_text_for_memory(text)
        unredacted = redact_vision_text_for_memory(text, vision_memory_write_enabled=True)

        self.assertIn("[vision context redacted]", redacted)
        self.assertNotIn("secret text", redacted)
        self.assertEqual(unredacted, text)

    def test_write_memory_turn_records_skips_guild_raw_for_person_bound_turn(self) -> None:
        raw_calls = []
        vault_calls = []

        def record_identity(user_text: str, answer: str, *, source: str) -> dict:
            return {"ok": True, "user_text": user_text, "answer": answer, "source": source}

        result = write_memory_turn_records(
            123,
            " 안녕 ",
            " 응 ",
            room_key="room",
            person_key="person",
            session_memory_key="session",
            source="text",
            user_speaker="정훈",
            assistant_speaker="이블린",
            turn_id="turn-write-1",
            record_identity_turn=record_identity,
            append_raw_rows=lambda *args, **kwargs: raw_calls.append((args, kwargs)),
            append_vault_rows=lambda *args, **kwargs: vault_calls.append((args, kwargs)),
        )

        self.assertEqual(result.memory_user_text, "안녕")
        self.assertEqual(result.memory_answer, "응")
        self.assertTrue(result.vault_mirrored)
        self.assertEqual(result.identity_record_decision["source"], "text")
        self.assertEqual(result.rows[0]["evidence_id"], "turn:turn-write-1:user")
        self.assertEqual(len(raw_calls), 3)
        self.assertEqual(raw_calls[0][0][:2], (123, result.rows))
        self.assertEqual(raw_calls[0][1]["scope_type"], "room")
        self.assertEqual(raw_calls[1][1]["scope_type"], "person")
        self.assertEqual(raw_calls[2][1]["scope_type"], "session")
        self.assertEqual(vault_calls[0][1]["scope_labels"], ["guild", "room:room", "person:person", "session:session"])

        raw_calls.clear()
        write_memory_turn_records(
            123,
            "shared user",
            "shared answer",
            room_key="room",
            person_key=None,
            session_memory_key="session",
            source="text",
            turn_id="turn-write-2",
            record_identity_turn=record_identity,
            append_raw_rows=lambda *args, **kwargs: raw_calls.append((args, kwargs)),
            append_vault_rows=lambda *_args, **_kwargs: None,
        )
        self.assertEqual(len(raw_calls), 3)
        self.assertNotIn("scope_type", raw_calls[0][1])
        self.assertEqual(raw_calls[1][1]["scope_type"], "room")
        self.assertEqual(raw_calls[2][1]["scope_type"], "session")

    def test_write_memory_turn_records_marks_vault_failure(self) -> None:
        logs = []
        private_error = "PRIVATE_TOKEN=C:/private/memory-note.md"

        def fail_vault(*args, **kwargs) -> None:
            raise RuntimeError(private_error)

        result = write_memory_turn_records(
            123,
            "user",
            "answer",
            record_identity_turn=lambda *args, **kwargs: {"ok": True},
            append_raw_rows=lambda *args, **kwargs: None,
            append_vault_rows=fail_vault,
            log=logs.append,
        )

        self.assertFalse(result.vault_mirrored)
        self.assertEqual(
            logs,
            [
                "[MEMORY VAULT] daily mirror failed: "
                "errorType=RuntimeError"
            ],
        )
        self.assertNotIn(private_error, logs[0])

    def test_schedule_passes_turn_scope_id_into_raw_memory_records(self) -> None:
        captured = {}

        def write_records(*_args, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                memory_user_text="user",
                memory_answer="answer",
                vault_mirrored=True,
                identity_record_decision={},
            )

        deps = SimpleNamespace(
            write_memory_turn_records=write_records,
            vision_memory_write_enabled=False,
            record_self_identity_turn=lambda *_args, **_kwargs: {},
            append_raw_transcript_rows=lambda *_args, **_kwargs: None,
            append_turn_rows_to_memory_vault=lambda *_args, **_kwargs: None,
            schedule_memory_vault_maintenance=lambda *_args, **_kwargs: None,
            memory_refresh_inputs_for_turn=lambda **_kwargs: SimpleNamespace(),
            get_conversation_history=lambda **_kwargs: [],
            session_last_active_at={},
            needs_search_or_deep_routing=lambda *_args, **_kwargs: False,
            build_memory_writer_decision_for_turn=lambda **_kwargs: SimpleNamespace(),
            build_memory_writer_decision=lambda *_args, **_kwargs: None,
            build_memory_writer_decision_payload=lambda *_args, **_kwargs: {},
            plan_memory_writebehind_schedule=lambda *_args, **_kwargs: SimpleNamespace(
                action="skip",
                status="skipped",
                writebehind_reason="test",
            ),
            runtime_session_key=lambda *_args, **_kwargs: None,
            memory_writebehind_task_key=lambda *_args, **_kwargs: None,
            should_replace_existing_memory_task=lambda *_args, **_kwargs: False,
            mark_memory_writer_status=lambda *_args, **_kwargs: None,
            memory_writebehind_status_log=None,
            background_memory_tasks={},
            create_turn_scoped_task=lambda *_args, **_kwargs: None,
            run_memory_writebehind_steps=lambda *_args, **_kwargs: None,
            update_long_term_memory=lambda *_args, **_kwargs: None,
            update_cognitive_state=lambda *_args, **_kwargs: None,
            log=lambda *_args, **_kwargs: None,
        )

        schedule_memory_update_from_runtime(
            123,
            "user",
            "answer",
            deps=deps,
            turn_scope=SimpleNamespace(turn_id="turn-scope-42"),
            runtime_mode="realtime",
        )

        self.assertEqual(captured["turn_id"], "turn-scope-42")

    def test_memory_writer_decision_payload_adds_runtime_metadata(self) -> None:
        class Decision:
            def to_dict(self) -> dict:
                return {"reason": "refresh"}

        payload = build_memory_writer_decision_payload(
            Decision(),
            source="voice",
            session_key="session",
            raw_transcript_written=True,
            vault_mirrored=False,
            identity_record_decision={"candidate": True},
        )

        self.assertEqual(payload["reason"], "refresh")
        self.assertEqual(payload["source"], "voice")
        self.assertEqual(payload["session_key"], "session")
        self.assertTrue(payload["raw_transcript_written"])
        self.assertFalse(payload["vault_mirrored"])
        self.assertEqual(payload["identity_review_candidate"], {"candidate": True})

    def test_memory_refresh_inputs_use_session_history_and_idle_gap(self) -> None:
        inputs = memory_refresh_inputs_for_turn(
            user_text="최신 정보 찾아줘",
            source="text",
            session_key="session",
            guild_id=123,
            history_reader=lambda **kwargs: ["u1", "a1", "u2"],
            last_active_at={"session": 90.0},
            deep_routing_needed=lambda text, *, source: "최신" in text and source == "text",
            now=lambda: 120.0,
        )

        self.assertEqual(inputs.turn_index, 2)
        self.assertEqual(inputs.idle_gap_sec, 30.0)
        self.assertTrue(inputs.deep_routing_needed)

    def test_build_memory_writer_decision_for_turn_passes_refresh_policy_to_builder(self) -> None:
        calls = []

        def builder(**kwargs):
            calls.append(kwargs)
            return {"decision": kwargs["should_refresh_memory"]}

        decision = build_memory_writer_decision_for_turn(
            user_text="나는 설정을 기억해줘",
            answer="기억할게",
            source="text",
            runtime_mode="batch",
            refresh_inputs=MemoryRefreshInputs(turn_index=1, idle_gap_sec=0.0, deep_routing_needed=False),
            decision_builder=builder,
        )

        self.assertEqual(decision, {"decision": True})
        self.assertEqual(calls[0]["runtime_mode"], "batch")
        self.assertTrue(calls[0]["should_refresh_memory"])

    def test_plan_memory_writebehind_schedule_handles_skip_and_realtime(self) -> None:
        class Decision:
            def __init__(self, should_run: bool) -> None:
                self.should_run = should_run

            def should_run_summary_llm(self) -> bool:
                return self.should_run

        skipped = plan_memory_writebehind_schedule(
            Decision(False),
            mode="normal",
            guild_id=123,
            session_memory_key=None,
            room_key=None,
            session_key=None,
            decision_payload={},
            runtime_session_key=lambda **kwargs: "runtime",
            task_key_builder=lambda base, payload: base,
            should_replace_task=lambda payload: False,
        )
        deferred = plan_memory_writebehind_schedule(
            Decision(True),
            mode="realtime",
            guild_id=123,
            session_memory_key="session-memory",
            room_key="room",
            session_key="session",
            decision_payload={},
            runtime_session_key=lambda **kwargs: "runtime",
            task_key_builder=lambda base, payload: base,
            should_replace_task=lambda payload: False,
        )

        self.assertEqual(skipped.action, "skip")
        self.assertEqual(skipped.status, "skipped")
        self.assertEqual(skipped.writebehind_reason, "summary_llm_not_needed")
        self.assertFalse(skipped.should_queue)
        self.assertEqual(deferred.action, "defer")
        self.assertEqual(deferred.status, "deferred")
        self.assertEqual(deferred.writebehind_reason, "runtime_mode_realtime")

    def test_plan_memory_writebehind_schedule_handles_batch_and_normal_queue(self) -> None:
        class Decision:
            def should_run_summary_llm(self) -> bool:
                return True

        batch = plan_memory_writebehind_schedule(
            Decision(),
            mode="batch",
            guild_id=123,
            session_memory_key="session-memory",
            room_key="room",
            session_key="session",
            decision_payload={"reason": "raw_only"},
            runtime_session_key=lambda **kwargs: "runtime",
            task_key_builder=lambda base, payload: f"{base}:batched",
            should_replace_task=lambda payload: True,
        )
        normal = plan_memory_writebehind_schedule(
            Decision(),
            mode="normal",
            guild_id=123,
            session_memory_key=None,
            room_key=None,
            session_key=None,
            decision_payload={},
            runtime_session_key=lambda **kwargs: "runtime",
            task_key_builder=lambda base, payload: base,
            should_replace_task=lambda payload: False,
        )

        self.assertEqual(batch.action, "batch")
        self.assertEqual(batch.status, "queued")
        self.assertEqual(batch.writebehind_mode, "batch")
        self.assertEqual(batch.task_key, "session-memory:batched")
        self.assertTrue(batch.replace_existing)
        self.assertTrue(batch.should_queue)
        self.assertEqual(normal.action, "normal")
        self.assertEqual(normal.writebehind_mode, "normal")
        self.assertIsNone(normal.task_key)


if __name__ == "__main__":
    unittest.main()
