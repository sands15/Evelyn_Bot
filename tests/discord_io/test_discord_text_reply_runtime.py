from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_text_reply_runtime import (  # noqa: E402
    BufferedEditStreamer,
    DiscordEditSink,
    DiscordTextReplyRuntimeDeps,
    stream_text_reply_from_runtime,
)


class DiscordTextReplyRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_buffered_edit_streamer_flushes_on_threshold_and_force_close(self) -> None:
        edits: list[str] = []
        times = iter([10.0, 10.1, 10.4])
        message = SimpleNamespace(
            content="hello",
            edit=lambda **kwargs: edits.append(kwargs["content"]),
        )

        async def edit(**kwargs: Any) -> None:
            edits.append(kwargs["content"])

        message.edit = edit
        streamer = BufferedEditStreamer(
            message,
            session_key="session-1",
            format_display_text=lambda text, **_kwargs: str(text).strip(),
            monotonic=lambda: next(times),
            min_edit_interval_ms=300,
            min_delta_chars=4,
            max_hold_ms=900,
        )

        await streamer.push("hello wo")
        await streamer.push("hello world")
        await streamer.close("done")

        self.assertEqual(edits, ["hello world", "done"])
        self.assertEqual(streamer.rendered_text, "done")

    async def test_discord_edit_sink_accumulates_chunks(self) -> None:
        pushed: list[tuple[str, bool]] = []

        class FakeStreamer:
            async def push(self, text: str, *, force: bool = False) -> None:
                pushed.append((text, force))

            async def close(self, text: str) -> None:
                pushed.append((text, True))

        sink = DiscordEditSink(FakeStreamer())

        await sink.on_chunk("he")
        await sink.on_chunk("")
        await sink.on_chunk("llo")
        await sink.close("hello")

        self.assertEqual(pushed, [("he", False), ("hello", False), ("hello", True)])

    async def test_stream_text_reply_builds_delivery_and_records_proactive_question(self) -> None:
        calls: list[tuple[str, Any]] = []

        async def ask_llm_streaming(user_text: str, **kwargs: Any) -> str:
            calls.append(("ask_llm", (user_text, kwargs["session_key"], kwargs["source"])))
            kwargs["on_first_chunk"]()
            return "answer"

        async def send_discord_text(channel: Any, text: str) -> Any:
            calls.append(("send", (channel.name, text)))
            return SimpleNamespace(message=SimpleNamespace(id=77, content=text))

        def maybe_append_proactive_question(answer: str, **kwargs: Any) -> tuple[str, bool]:
            calls.append(("proactive", (answer, kwargs["awaiting_user_reply"])))
            return f"{answer} follow-up?", True

        def build_delivery_plan(answer_payload: Any, **kwargs: Any) -> Any:
            calls.append(("delivery", (answer_payload.display_text, kwargs["include_voice"], kwargs["text_message"])))
            return SimpleNamespace(should_play_voice=kwargs["include_voice"], text_message=kwargs["text_message"])

        deps = DiscordTextReplyRuntimeDeps(
            attach_current_task=lambda turn_scope: calls.append(("attach", turn_scope)) or "task-1",
            detach_task=lambda turn_scope, task: calls.append(("detach", (turn_scope, task))),
            new_turn_metrics=lambda **kwargs: {"meta": {"topic_id": kwargs["topic_id"]}, "marks": {}},
            session_topic_id=lambda session_key: f"topic:{session_key}",
            ask_llm_streaming=ask_llm_streaming,
            log_llm_first_chunk=lambda metrics: calls.append(("first_chunk", metrics["meta"]["topic_id"])),
            session_state_snapshot=lambda session_key: {"awaiting_user_reply": True},
            maybe_append_proactive_question=maybe_append_proactive_question,
            update_session_state=lambda session_key, **kwargs: calls.append(("update_session", (session_key, kwargs))),
            build_answer_payload_from_text=lambda answer: SimpleNamespace(display_text=answer),
            format_display_text=lambda text, **kwargs: f"[display] {text}",
            fallback_answer_for=lambda user_text: "fallback",
            build_delivery_plan=build_delivery_plan,
            split_tts_sentences=lambda text: [text],
            send_discord_text=send_discord_text,
        )

        answer, sent_message, metrics, delivery_plan = await stream_text_reply_from_runtime(
            SimpleNamespace(name="chan"),
            "hello",
            guild_id=1,
            session_key="session-1",
            turn_id="turn-1",
            room_key="room",
            person_key="person",
            session_memory_key="session-memory",
            include_voice=True,
            turn_scope="scope-1",
            proactive_resolution={"resolved": False},
            deps=deps,
        )

        self.assertEqual(answer, "answer follow-up?")
        self.assertEqual(sent_message.content, "[display] answer follow-up?")
        self.assertTrue(delivery_plan.should_play_voice)
        self.assertTrue(metrics["meta"]["needs_tts"])
        self.assertEqual(metrics["meta"]["proactive_question_resolution"], {"resolved": False})
        self.assertIn(("first_chunk", "topic:session-1"), calls)
        self.assertIn(("update_session", ("session-1", {
            "speaker": "assistant",
            "awaiting_user_reply": True,
            "answer_text": "answer follow-up?",
            "user_text": "hello",
        })), calls)
        self.assertEqual(calls[-1], ("detach", ("scope-1", "task-1")))

    async def test_resolved_proactive_question_skips_append_and_uses_fallback_display(self) -> None:
        calls: list[str] = []

        async def ask_llm_streaming(_user_text: str, **kwargs: Any) -> str:
            kwargs["on_first_chunk"]()
            return ""

        async def send_discord_text(_channel: Any, text: str) -> Any:
            return SimpleNamespace(message=SimpleNamespace(content=text))

        deps = DiscordTextReplyRuntimeDeps(
            attach_current_task=lambda _turn_scope: "task-1",
            detach_task=lambda _turn_scope, _task: calls.append("detach"),
            new_turn_metrics=lambda **_kwargs: {"meta": {}, "marks": {}},
            session_topic_id=lambda _session_key: None,
            ask_llm_streaming=ask_llm_streaming,
            log_llm_first_chunk=lambda _metrics: calls.append("first_chunk"),
            session_state_snapshot=lambda _session_key: {"awaiting_user_reply": False},
            maybe_append_proactive_question=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
            update_session_state=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
            build_answer_payload_from_text=lambda _answer: SimpleNamespace(display_text=""),
            format_display_text=lambda _text, **_kwargs: "",
            fallback_answer_for=lambda user_text: f"fallback:{user_text}",
            build_delivery_plan=lambda _payload, **kwargs: SimpleNamespace(text_message=kwargs["text_message"]),
            split_tts_sentences=lambda text: [text],
            send_discord_text=send_discord_text,
        )

        answer, sent_message, _metrics, delivery_plan = await stream_text_reply_from_runtime(
            SimpleNamespace(),
            "hello",
            guild_id=1,
            session_key="session-1",
            proactive_resolution={"resolved": True},
            deps=deps,
        )

        self.assertEqual(answer, "")
        self.assertEqual(sent_message.content, "fallback:hello")
        self.assertEqual(delivery_plan.text_message, "fallback:hello")
        self.assertEqual(calls, ["first_chunk", "detach"])


if __name__ == "__main__":
    unittest.main()
