import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_delivery import (  # noqa: E402
    DiscordStreamingVoiceDeliveryRequest,
    build_streaming_voice_delivery,
    execute_streaming_voice_delivery_plan,
    send_discord_text,
)


class FakeChannel:
    def __init__(self, *, fail_referenced_send: bool = False) -> None:
        self.fail_referenced_send = fail_referenced_send
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def send(self, text: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((text, kwargs))
        if self.fail_referenced_send and "reference" in kwargs:
            raise RuntimeError("referenced send failed")
        return {"text": text, "kwargs": kwargs}


class DiscordDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_discord_text_sends_plain_text(self) -> None:
        channel = FakeChannel()

        result = await send_discord_text(channel, "hello")

        self.assertEqual(channel.calls, [("hello", {})])
        self.assertFalse(result.used_reference)
        self.assertFalse(result.fallback_used)
        self.assertEqual(result.message["text"], "hello")

    async def test_send_discord_text_uses_reference_when_available(self) -> None:
        channel = FakeChannel()

        result = await send_discord_text(
            channel,
            "hello",
            reference_message_id="42",
            reference_factory=lambda message_id: {"id": message_id},
        )

        self.assertEqual(channel.calls, [("hello", {"reference": {"id": 42}})])
        self.assertTrue(result.used_reference)
        self.assertFalse(result.fallback_used)

    async def test_send_discord_text_falls_back_when_reference_send_fails(self) -> None:
        channel = FakeChannel(fail_referenced_send=True)

        result = await send_discord_text(
            channel,
            "hello",
            reference_message_id="42",
            reference_factory=lambda message_id: {"id": message_id},
        )

        self.assertEqual(
            channel.calls,
            [
                ("hello", {"reference": {"id": 42}}),
                ("hello", {}),
            ],
        )
        self.assertFalse(result.used_reference)
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.message["text"], "hello")

    async def test_build_streaming_voice_delivery_queues_chunks_to_stream_task(self) -> None:
        metrics: dict[str, Any] = {}

        async def stream_tts_sentences(voice_client: Any, sentence_queue: Any, **kwargs: Any) -> None:
            consumed = []
            while True:
                item = await sentence_queue.get()
                if item is None:
                    break
                consumed.append(item)
            metrics["voice_client"] = voice_client
            metrics["stream_kwargs"] = kwargs
            metrics["consumed"] = consumed

        delivery = build_streaming_voice_delivery(
            DiscordStreamingVoiceDeliveryRequest(
                voice_client="vc",
                metrics=metrics,
                turn_id="turn-1",
                session_key="session-1",
                turn_scope="scope-1",
                stream_tts_sentences=stream_tts_sentences,
                create_playback_task=lambda coro, _scope: asyncio.create_task(coro),
                log_stage=None,
                prefetch_chunks=1,
            )
        )

        await delivery.on_chunk("hello")
        await delivery.close("hello")
        queued = await delivery.finalize()

        self.assertEqual(queued, 1)
        self.assertEqual(metrics["voice_client"], "vc")
        self.assertEqual(metrics["stream_kwargs"]["turn_id"], "turn-1")
        self.assertEqual(metrics["stream_kwargs"]["session_key"], "session-1")
        self.assertEqual(metrics["stream_kwargs"]["turn_scope"], "scope-1")
        self.assertEqual(metrics["consumed"], ["hello"])

    async def test_execute_streaming_voice_delivery_plan_skips_non_voice_plan(self) -> None:
        result = await execute_streaming_voice_delivery_plan(
            SimpleNamespace(should_play_voice=False, tts_chunks=["hello"], text_message="hello"),
            start_delivery=lambda: (_ for _ in ()).throw(AssertionError("should not start")),
        )

        self.assertEqual(result, 0)

    async def test_execute_streaming_voice_delivery_plan_runs_delivery(self) -> None:
        class FakeDelivery:
            def __init__(self) -> None:
                self.chunks: list[str] = []
                self.closed_with: str | None = None
                self.aborted = False

            async def on_chunk(self, chunk: str) -> None:
                self.chunks.append(chunk)

            async def close(self, final_text: str) -> None:
                self.closed_with = final_text

            async def finalize(self) -> int:
                return len(self.chunks)

            async def abort(self) -> None:
                self.aborted = True

        delivery = FakeDelivery()

        result = await execute_streaming_voice_delivery_plan(
            SimpleNamespace(should_play_voice=True, tts_chunks=["a", "b"], text_message="done"),
            start_delivery=lambda: delivery,
        )

        self.assertEqual(result, 2)
        self.assertEqual(delivery.chunks, ["a", "b"])
        self.assertEqual(delivery.closed_with, "done")
        self.assertTrue(delivery.aborted)


if __name__ == "__main__":
    unittest.main()
