from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

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


@dataclass
class RuntimeHarness:
    delivery: FakeDelivery = field(default_factory=FakeDelivery)
    local_delivery: FakeDelivery = field(default_factory=lambda: FakeDelivery(queued_count=0))
    playback_count: int = 0
    fallback_spoken: list[str] = field(default_factory=list)
    marks: list[dict[str, Any]] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    detached: bool = False

    def deps(self) -> VoiceDeliveryRuntimeDeps:
        async def ask_llm_streaming(user_text: str, **kwargs: Any) -> str:
            on_sentence = kwargs.get("on_sentence")
            if on_sentence is not None:
                await on_sentence("첫 문장")
            return f"{user_text} 답변"

        async def speak_answer_local(answer: str, **_kwargs: Any) -> bool:
            self.fallback_spoken.append(answer)
            self.playback_count += 1
            return True

        return VoiceDeliveryRuntimeDeps(
            attach_current_task=lambda _scope: None,
            detach_task=lambda _scope, _task: setattr(self, "detached", True),
            current_turn_id=lambda _session_key: "turn-1",
            session_topic_id=lambda _session_key: "topic-1",
            new_turn_metrics=lambda **kwargs: {"started_at": 1.0, "marks": {}, "meta": dict(kwargs)},
            is_local_speaker_voice_client=lambda vc: vc == "local",
            start_streaming_voice_delivery=lambda *_args, **_kwargs: self.delivery,
            start_streaming_local_voice_delivery=lambda **_kwargs: self.local_delivery,
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


if __name__ == "__main__":
    unittest.main()
