from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import local_tts_playback  # noqa: E402
from evelyn_core.local_tts_playback import LocalTtsPlaybackManager  # noqa: E402
from evelyn_core.voice_delivery_runtime import (  # noqa: E402
    VoiceDeliveryRuntimeDeps,
    ask_llm_and_speak_local_from_runtime,
    ask_llm_and_speak_streaming_from_runtime,
)


class FakeDelivery:
    def __init__(self, *, queued_count: int = 1) -> None:
        self.queued_count = queued_count
        self.chunks: list[str] = []
        self.closed_text = ""
        self.finalized = False
        self.aborted = False

    async def on_chunk(self, text: str) -> None:
        self.chunks.append(text)

    async def close(self, final_text: str) -> None:
        self.closed_text = final_text

    async def finalize(self) -> int:
        self.finalized = True
        return self.queued_count

    async def abort(self) -> None:
        self.aborted = True


class PartialWriteFailureDelivery(FakeDelivery):
    def __init__(self, *, metrics: dict[str, Any], manager: LocalTtsPlaybackManager) -> None:
        super().__init__(queued_count=1)
        self.metrics = metrics
        self.manager = manager
        self.playback_ok: bool | None = None

    async def finalize(self) -> int:
        self.finalized = True

        class OneChunkSource:
            def __init__(self) -> None:
                self.chunks = [b"partial-answer"]

            def read(self) -> bytes:
                return self.chunks.pop(0) if self.chunks else b""

            def cleanup(self) -> None:
                return None

        self.playback_ok = await self.manager.play_source(
            OneChunkSource(),
            metrics=self.metrics,
        )
        return self.queued_count


@dataclass
class RuntimeHarness:
    delivery: FakeDelivery = field(default_factory=FakeDelivery)
    local_delivery: FakeDelivery = field(default_factory=lambda: FakeDelivery(queued_count=0))
    playback_count: int = 0
    fallback_spoken: list[str] = field(default_factory=list)
    marks: list[dict[str, Any]] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    detached: bool = False
    answer_text: str | None = None
    emit_sentence: bool = True
    local_delivery_manager: LocalTtsPlaybackManager | None = None

    def deps(self) -> VoiceDeliveryRuntimeDeps:
        async def ask_llm_streaming(user_text: str, **kwargs: Any) -> str:
            answer = self.answer_text if self.answer_text is not None else f"{user_text} 답변"
            on_sentence = kwargs.get("on_sentence")
            if on_sentence is not None and answer and self.emit_sentence:
                await on_sentence("첫 문장")
            return answer

        async def speak_answer_local(answer: str, **_kwargs: Any) -> bool:
            self.fallback_spoken.append(answer)
            self.playback_count += 1
            return True

        def start_streaming_local_voice_delivery(**kwargs: Any) -> FakeDelivery:
            if self.local_delivery_manager is not None:
                self.local_delivery = PartialWriteFailureDelivery(
                    metrics=kwargs["metrics"],
                    manager=self.local_delivery_manager,
                )
            return self.local_delivery

        return VoiceDeliveryRuntimeDeps(
            attach_current_task=lambda _scope: None,
            detach_task=lambda _scope, _task: setattr(self, "detached", True),
            current_turn_id=lambda _session_key: "turn-1",
            session_topic_id=lambda _session_key: "topic-1",
            new_turn_metrics=lambda **kwargs: {"started_at": 1.0, "marks": {}, "meta": dict(kwargs)},
            is_local_speaker_voice_client=lambda vc: vc == "local",
            start_streaming_voice_delivery=lambda *_args, **_kwargs: self.delivery,
            start_streaming_local_voice_delivery=start_streaming_local_voice_delivery,
            ask_llm_streaming=ask_llm_streaming,
            speak_answer_local=speak_answer_local,
            local_playback_count=lambda: self.playback_count,
            mark_barge_in_continuity_probe=lambda _metrics, **kwargs: self.marks.append(kwargs),
            record_voice_pipeline_failure=lambda *_args, **_kwargs: None,
            log_voice_latency=lambda *_args, **_kwargs: None,
            log_voice_stage=lambda *_args, **_kwargs: None,
            log_voice_bottleneck_summary=lambda _metrics, **kwargs: self.summaries.append(str(kwargs.get("extra") or "")),
            false_trigger_reason_code="false_trigger",
            false_trigger_reason_label="false trigger",
        )


class VoiceDeliveryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_discord_streaming_finalizes_delivery(self) -> None:
        harness = RuntimeHarness()
        metrics = {"started_at": 1.0, "marks": {}, "meta": {}}

        answer = await ask_llm_and_speak_streaming_from_runtime(
            "discord",
            "테스트",
            deps=harness.deps(),
            guild_id=1,
            session_key="s1",
            metrics=metrics,
        )

        self.assertEqual(answer, "테스트 답변")
        self.assertEqual(harness.delivery.chunks, ["첫 문장"])
        self.assertEqual(harness.delivery.closed_text, "테스트 답변")
        self.assertTrue(harness.delivery.finalized)
        self.assertTrue(harness.delivery.aborted)
        self.assertTrue(harness.detached)
        self.assertIs(metrics["meta"]["reply_started"], True)
        self.assertIs(metrics["meta"]["reply_final"], True)
        self.assertIn({"success": True, "reason": "finalize_complete", "queued_sentence_count": 1}, harness.marks)
        self.assertTrue(any("mode=llm_streaming" in summary for summary in harness.summaries))

    async def test_local_streaming_uses_full_answer_fallback_when_nothing_played(self) -> None:
        harness = RuntimeHarness()
        metrics = {"started_at": 1.0, "marks": {}, "meta": {}}

        answer = await ask_llm_and_speak_local_from_runtime(
            "local",
            "로컬",
            deps=harness.deps(),
            guild_id=1,
            session_key="s1",
            metrics=metrics,
        )

        self.assertEqual(answer, "로컬 답변")
        self.assertEqual(harness.local_delivery.chunks, ["첫 문장"])
        self.assertEqual(harness.fallback_spoken, ["로컬 답변"])
        self.assertEqual(metrics["meta"]["local_streaming_tts_fallback_reason"], "no_sentence_queued")
        self.assertTrue(metrics["meta"]["local_streaming_tts_fallback_used"])
        self.assertEqual(metrics["meta"]["delivery_mode"], "llm_sentence_stream")
        self.assertTrue(harness.local_delivery.aborted)
        self.assertTrue(any("fallback=True" in summary for summary in harness.summaries))

    async def test_partial_device_write_never_replays_full_answer_fallback(self) -> None:
        class PartialWriteFailureStream:
            writes: list[bytes] = []

            def __init__(self, **_kwargs: Any) -> None:
                pass

            def __enter__(self) -> "PartialWriteFailureStream":
                return self

            def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
                return None

            def write(self, chunk: bytes) -> None:
                type(self).writes.append(bytes(chunk))
                raise OSError("device failed after accepting bytes")

        class PartialWriteFailureSoundDevice:
            RawOutputStream = PartialWriteFailureStream

        original_sd = local_tts_playback.sd
        local_tts_playback.sd = PartialWriteFailureSoundDevice()
        manager = LocalTtsPlaybackManager(enabled=True)
        harness = RuntimeHarness(local_delivery_manager=manager)
        metrics = {"started_at": 1.0, "marks": {}, "meta": {}}
        try:
            answer = await ask_llm_and_speak_local_from_runtime(
                "local",
                "로컬",
                deps=harness.deps(),
                guild_id=1,
                session_key="s1",
                metrics=metrics,
            )
        finally:
            local_tts_playback.sd = original_sd

        self.assertEqual(answer, "로컬 답변")
        self.assertEqual(PartialWriteFailureStream.writes, [b"partial-answer"])
        self.assertFalse(harness.local_delivery.playback_ok)
        self.assertIs(metrics["meta"]["local_tts_playback_attempted"], True)
        self.assertEqual(harness.fallback_spoken, [])
        self.assertNotIn("local_streaming_tts_fallback_used", metrics["meta"])
        self.assertEqual(
            metrics["meta"]["local_streaming_tts_fallback_suppressed_reason"],
            "playback_attempted",
        )
        self.assertTrue(any("fallback=False" in summary for summary in harness.summaries))

    async def test_stale_validation_attempt_blocks_device_and_full_answer_fallback(self) -> None:
        class RecordingStream:
            writes: list[bytes] = []

            def __init__(self, **_kwargs: Any) -> None:
                pass

            def __enter__(self) -> "RecordingStream":
                return self

            def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
                return None

            def write(self, chunk: bytes) -> None:
                type(self).writes.append(bytes(chunk))

        class RecordingSoundDevice:
            RawOutputStream = RecordingStream

        original_sd = local_tts_playback.sd
        local_tts_playback.sd = RecordingSoundDevice()
        manager = LocalTtsPlaybackManager(enabled=True)
        harness = RuntimeHarness(local_delivery_manager=manager)
        metrics = {
            "started_at": 1.0,
            "marks": {},
            "meta": {
                "validation_session_id": "validation-1",
                "validation_step_id": "local-step-1",
                "validation_attempt_id": "stale-attempt-1",
            },
        }
        try:
            with patch.object(
                local_tts_playback,
                "validation_attempt_binding_is_current",
                return_value=False,
            ) as validate:
                answer = await ask_llm_and_speak_local_from_runtime(
                    "local",
                    "로컬",
                    deps=harness.deps(),
                    guild_id=1,
                    session_key="s1",
                    metrics=metrics,
                )
        finally:
            local_tts_playback.sd = original_sd

        self.assertEqual(answer, "로컬 답변")
        self.assertEqual(RecordingStream.writes, [])
        self.assertFalse(harness.local_delivery.playback_ok)
        self.assertEqual(harness.fallback_spoken, [])
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(validate.call_args.kwargs["surface"], "local")
        self.assertIs(
            metrics["meta"]["local_tts_playback_terminal_no_fallback"],
            True,
        )
        self.assertEqual(
            metrics["meta"]["local_streaming_tts_fallback_suppressed_reason"],
            "validation_attempt_stale",
        )
        self.assertNotIn("local_streaming_tts_fallback_used", metrics["meta"])
        self.assertEqual(manager.snapshot()["playCount"], 0)
        self.assertTrue(any("fallback=False" in summary for summary in harness.summaries))

    async def test_empty_streaming_reply_is_typed_as_delivery_failure_before_summary(self) -> None:
        harness = RuntimeHarness(answer_text="")
        metrics = {"started_at": 1.0, "marks": {}, "meta": {}}

        answer = await ask_llm_and_speak_streaming_from_runtime(
            "discord",
            "테스트",
            deps=harness.deps(),
            guild_id=1,
            session_key="s1",
            metrics=metrics,
        )

        self.assertEqual(answer, "")
        self.assertIs(metrics["meta"]["reply_started"], False)
        self.assertIs(metrics["meta"]["reply_final"], False)
        self.assertEqual(
            metrics["meta"]["voice_delivery_failure_code"],
            "voice_delivery_empty",
        )
        self.assertTrue(harness.summaries)

    async def test_nonempty_final_without_sentence_marks_reply_started(self) -> None:
        harness = RuntimeHarness(emit_sentence=False)
        metrics = {"started_at": 1.0, "marks": {}, "meta": {}}

        answer = await ask_llm_and_speak_streaming_from_runtime(
            "discord",
            "테스트",
            deps=harness.deps(),
            guild_id=1,
            session_key="s1",
            metrics=metrics,
        )

        self.assertEqual(answer, "테스트 답변")
        self.assertEqual(harness.delivery.chunks, [])
        self.assertIs(metrics["meta"]["reply_started"], True)
        self.assertIs(metrics["meta"]["reply_final"], True)


if __name__ == "__main__":
    unittest.main()
