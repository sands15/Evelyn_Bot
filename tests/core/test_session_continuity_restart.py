from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
CRASH_EXIT_CODE = 74
UNATTRIBUTED_MEMORY_RECEIPT_REF = {
    "schema": "conversation.memory-receipt-ref.v1",
    "state": "unattributed",
    "memoryVersion": 0,
    "suppliedNoteIds": [],
    "suppliedNoteCount": 0,
    "contentFree": True,
}

WRITER_PROCESS = textwrap.dedent(
    f"""
    import asyncio
    import json
    import os
    import sys
    import time
    from pathlib import Path

    from evelyn_core.session_continuity import (
        SessionContinuityCheckpoint,
    )
    from evelyn_core.session_memory_state import SessionStateStore

    async def run():
        root = Path(sys.argv[1])
        checkpoint_path = root / "active.json"
        store = SessionStateStore.create_empty()
        manager = SessionContinuityCheckpoint(
            store=store,
            checkpoint_path=checkpoint_path,
            status_path=root / "status.json",
            system_prompt="old process system prompt",
            flush_interval_sec=0.25,
        )
        manager.ensure_started()
        session_key = "guild:7:text:8:user:42"
        store.append_history(
            session_key,
            "remember our synthetic plan",
            "I will continue after restart",
            system_prompt="old process system prompt",
            max_history_items=12,
        )
        store.update_session_state(
            session_key,
            user_id=42,
            speaker="assistant",
            awaiting_user_reply=True,
            topic_id="topic-restart",
            active_conversation_awaiting_reply_sec=300.0,
        )
        store.turn_ids[session_key] = "turn-before-crash"
        store.remember_followup_target(
            session_key,
            channel_id=8,
            message_id=99,
        )
        store.partial_stt_text[session_key] = (
            "partial transcript must not survive"
        )
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            try:
                payload = json.loads(
                    checkpoint_path.read_text(encoding="utf-8")
                )
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            sessions = payload.get("sessions", [])
            if (
                sessions
                and sessions[0].get("sessionKey") == session_key
            ):
                os._exit({CRASH_EXIT_CODE})
        os._exit(75)

    asyncio.run(run())
    """
)

RECOVERY_PROCESS = textwrap.dedent(
    """
    import json
    import sys
    import time
    from pathlib import Path

    from evelyn_core.session_continuity import (
        SessionContinuityCheckpoint,
    )
    from evelyn_core.session_memory_state import SessionStateStore

    root = Path(sys.argv[1])
    session_key = "guild:7:text:8:user:42"
    store = SessionStateStore.create_empty()
    manager = SessionContinuityCheckpoint(
        store=store,
        checkpoint_path=root / "active.json",
        status_path=root / "status.json",
        system_prompt="new process system prompt",
    )
    status = manager.restore()
    now_monotonic = time.monotonic()
    result = {
        "status": status,
        "history": store.histories.get(session_key, []),
        "activeForOwner": store.is_active_for_user(
            session_key,
            42,
            now_monotonic=now_monotonic,
        ),
        "activeForOtherUser": store.is_active_for_user(
            session_key,
            43,
            now_monotonic=now_monotonic,
        ),
        "activeRemainingSec": (
            store.active_until.get(session_key, 0.0)
            - now_monotonic
        ),
        "userId": store.active_user_ids.get(session_key),
        "awaitingUserReply": store.awaiting_user_reply.get(
            session_key
        ),
        "lastSpeaker": store.last_speaker.get(session_key),
        "topicId": store.topic_ids.get(session_key),
        "turnId": store.turn_ids.get(session_key),
        "followupTarget": store.followup_targets.get(session_key),
        "partialStt": store.partial_stt_text.get(session_key),
        "lastStt": store.last_stt_text.get(session_key),
    }
    print(json.dumps(result, ensure_ascii=False))
    """
)

ACCEPTED_VOICE_WRITER_PROCESS = textwrap.dedent(
    f"""
    import os
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    from evelyn_core.session_continuity import (
        SessionContinuityCheckpoint,
    )
    from evelyn_core.session_memory_state import SessionStateStore
    from evelyn_core.voice_orchestration import accept_voice_reply_execution
    from evelyn_core.voice_pipeline import (
        TranscriptResult,
        VoiceReplyRequest,
        VoiceSegment,
    )
    from evelyn_core.voice_reply_side_effects import (
        checkpoint_accepted_voice_turn_from_runtime,
    )

    root = Path(sys.argv[1])
    marker = root / "reply_started.marker"
    session_key = "guild:7:voice:8:user:42"
    store = SessionStateStore.create_empty()
    manager = SessionContinuityCheckpoint(
        store=store,
        checkpoint_path=root / "active.json",
        status_path=root / "status.json",
        system_prompt="old process system prompt",
    )

    def begin_user_only_turn(key, text, **kwargs):
        return store.begin_user_only_turn(
            key,
            text,
            system_prompt="old process system prompt",
            max_history_items=12,
            active_conversation_awaiting_reply_sec=300.0,
            **kwargs,
        )

    checkpoint_deps = SimpleNamespace(
        begin_user_only_turn=begin_user_only_turn,
        commit_session_continuity=manager.commit_completed_turn,
        log=lambda *_args, **_kwargs: None,
    )
    metrics = {{"meta": {{}}}}

    def checkpoint(**kwargs):
        checkpoint_accepted_voice_turn_from_runtime(
            **kwargs,
            deps=checkpoint_deps,
        )
        os._exit({CRASH_EXIT_CODE})

    def reply_started(*_args, **_kwargs):
        marker.write_text("started", encoding="utf-8")

    transcript = TranscriptResult(
        wake_detected=True,
        wake_match_mode="exact",
        wake_alias="이블린",
        probe_text="continue the accepted plan",
        confirm_text="continue the accepted plan",
        reject_reason=None,
        partial_text="",
        committed_text="continue the accepted plan",
        final_text="continue the accepted plan",
        speaker_user_id=42,
        duration_sec=0.1,
    )
    segment = VoiceSegment(
        guild_id=7,
        room_session_key="room-1",
        session_key=session_key,
        speaker_user_id=42,
        speaker_name="tester",
        audio16k=None,
        sampling_rate=16000,
        duration_sec=0.1,
        segment_id=1,
        owner_user_id=42,
    )
    reply = VoiceReplyRequest(
        transcript=transcript,
        segment=segment,
        gate_mode="wake_entry",
        raw_user_text="continue the accepted plan",
        prompt_user_text="continue the accepted plan",
        history_user_text="continue the accepted plan",
        wake_only_turn=False,
        turn_type="voice_request",
        selected_path="main_llm",
        reply_source="main_llm",
        topic_id="topic-voice-restart",
    )
    accept_voice_reply_execution(
        session_key=session_key,
        room_session_key="room-1",
        user_id=42,
        source_turn_id="turn-accepted-before-crash",
        segment_id=1,
        gate_mode="wake_entry",
        reply_in_progress=False,
        voice_reply=reply,
        voice_segment=segment,
        transcript=transcript,
        ingress_source="discord_voice",
        queue_wait_ms=0.0,
        active_conversation_awaiting_reply_sec=300.0,
        active_conversation_voice_sec=60.0,
        metrics=metrics,
        start_new_turn=lambda *_args, **_kwargs: reply_started(),
        update_session_state=lambda *_args, **_kwargs: reply_started(),
        checkpoint_accepted_voice_turn=checkpoint,
        set_room_owner=reply_started,
        session_partial_stt_text={{}},
        session_committed_stt_text={{}},
        partial_stt_cache={{}},
        owner_user_id=42,
        make_turn_scope=lambda _turn_id: reply_started(),
        replace_room_turn_scope=reply_started,
        attach_current_task=reply_started,
        set_room_reply_in_progress=reply_started,
    )
    os._exit(75)
    """
)

ACCEPTED_VOICE_RECOVERY_PROCESS = textwrap.dedent(
    """
    import json
    import sys
    from pathlib import Path

    from evelyn_core.session_continuity import (
        SessionContinuityCheckpoint,
    )
    from evelyn_core.session_memory_state import SessionStateStore

    root = Path(sys.argv[1])
    session_key = "guild:7:voice:8:user:42"
    store = SessionStateStore.create_empty()
    manager = SessionContinuityCheckpoint(
        store=store,
        checkpoint_path=root / "active.json",
        status_path=root / "status.json",
        system_prompt="new process system prompt",
    )
    status = manager.restore()
    history = store.histories.get(session_key, [])
    print(json.dumps({
        "status": status.get("state"),
        "history": history,
        "turnId": store.turn_ids.get(session_key),
        "replyStarted": (root / "reply_started.marker").exists(),
    }, ensure_ascii=False))
    """
)


class SessionContinuityRestartTests(unittest.TestCase):
    def subprocess_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item
            for item in (str(RUNTIME_ROOT), existing)
            if item
        )
        return environment

    def test_periodic_checkpoint_restores_after_ungraceful_exit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            writer = subprocess.run(
                [sys.executable, "-c", WRITER_PROCESS, str(root)],
                cwd=REPO_ROOT,
                env=self.subprocess_environment(),
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(
                writer.returncode,
                CRASH_EXIT_CODE,
                writer.stderr + writer.stdout,
            )

            checkpoint_path = root / "active.json"
            checkpoint = json.loads(
                checkpoint_path.read_text(encoding="utf-8")
            )
            serialized_checkpoint = json.dumps(
                checkpoint,
                ensure_ascii=False,
            )
            self.assertIn(
                "remember our synthetic plan",
                serialized_checkpoint,
            )
            self.assertIn(
                "I will continue after restart",
                serialized_checkpoint,
            )
            self.assertNotIn(
                "old process system prompt",
                serialized_checkpoint,
            )
            self.assertNotIn(
                "partial transcript must not survive",
                serialized_checkpoint,
            )
            self.assertFalse(checkpoint["policy"]["rawAudio"])
            self.assertFalse(
                checkpoint["policy"]["partialTranscript"]
            )
            self.assertFalse(checkpoint["policy"]["systemPrompt"])

            recovery = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    RECOVERY_PROCESS,
                    str(root),
                ],
                cwd=REPO_ROOT,
                env=self.subprocess_environment(),
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(
                recovery.returncode,
                0,
                recovery.stderr + recovery.stdout,
            )
            result = json.loads(recovery.stdout)
            status_raw = (
                root / "status.json"
            ).read_text(encoding="utf-8")
            head = json.loads(
                (
                    root / "checkpoint_head.json"
                ).read_text(encoding="utf-8")
            )

        self.assertEqual(result["status"]["state"], "restored")
        self.assertEqual(
            result["status"]["checkpointIntegrity"],
            "verified",
        )
        self.assertEqual(
            result["status"]["checkpointHeadState"],
            "current",
        )
        self.assertTrue(
            result["status"]["rollbackProtected"]
        )
        self.assertEqual(head["state"], "active")
        self.assertEqual(
            head["checkpointHash"],
            checkpoint["checkpointHash"],
        )
        self.assertEqual(
            result["status"]["restoredSessionCount"],
            1,
        )
        self.assertEqual(
            result["history"],
            [
                {
                    "role": "system",
                    "content": "new process system prompt",
                },
                {
                    "role": "user",
                    "content": "remember our synthetic plan",
                },
                {
                    "role": "assistant",
                    "content": "I will continue after restart",
                    "memoryReceiptRef": (
                        UNATTRIBUTED_MEMORY_RECEIPT_REF
                    ),
                },
            ],
        )
        self.assertTrue(result["activeForOwner"])
        self.assertFalse(result["activeForOtherUser"])
        self.assertGreater(result["activeRemainingSec"], 250.0)
        self.assertLessEqual(result["activeRemainingSec"], 300.0)
        self.assertEqual(result["userId"], 42)
        self.assertTrue(result["awaitingUserReply"])
        self.assertEqual(result["lastSpeaker"], "assistant")
        self.assertEqual(result["topicId"], "topic-restart")
        self.assertEqual(result["turnId"], "turn-before-crash")
        self.assertEqual(
            result["followupTarget"],
            {"channel_id": 8, "message_id": 99},
        )
        self.assertIsNone(result["partialStt"])
        self.assertIsNone(result["lastStt"])
        self.assertNotIn(
            "remember our synthetic plan",
            status_raw,
        )
        self.assertNotIn(
            "I will continue after restart",
            status_raw,
        )

    def test_accepted_voice_user_turn_restores_after_immediate_hard_exit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            writer = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    ACCEPTED_VOICE_WRITER_PROCESS,
                    str(root),
                ],
                cwd=REPO_ROOT,
                env=self.subprocess_environment(),
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(
                writer.returncode,
                CRASH_EXIT_CODE,
                writer.stderr + writer.stdout,
            )

            recovery = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    ACCEPTED_VOICE_RECOVERY_PROCESS,
                    str(root),
                ],
                cwd=REPO_ROOT,
                env=self.subprocess_environment(),
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(
                recovery.returncode,
                0,
                recovery.stderr + recovery.stdout,
            )
            result = json.loads(recovery.stdout)

        self.assertEqual(result["status"], "restored")
        self.assertEqual(
            result["history"],
            [
                {
                    "role": "system",
                    "content": "new process system prompt",
                },
                {
                    "role": "user",
                    "content": "continue the accepted plan",
                },
            ],
        )
        self.assertEqual(
            result["turnId"],
            "turn-accepted-before-crash",
        )
        self.assertFalse(result["replyStarted"])


if __name__ == "__main__":
    unittest.main()
