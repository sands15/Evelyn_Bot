from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_text_turn import DiscordTextMessageHandlerDeps, handle_discord_text_message  # noqa: E402
from evelyn_core.conversation_ingress_composition import (  # noqa: E402
    ConversationIngressComposition,
    ConversationIngressCompositionDeps,
)
from evelyn_core.conversation_ingress_recovery import (  # noqa: E402
    ConversationIngressRecoveryJournal,
)
from evelyn_core.conversation_memory_receipt import (  # noqa: E402
    not_used_memory_receipt_ref,
    unattributed_memory_receipt_ref,
)
from evelyn_core.discord_ingress import build_text_ingress_context  # noqa: E402
from evelyn_core.discord_runtime_status import DiscordRuntimeStatus  # noqa: E402
from evelyn_core.memory_confirmation_contract import memory_owner_scope  # noqa: E402
from evelyn_core.session_memory_state import SessionStateStore  # noqa: E402
from tests.continuity_test_support import (  # noqa: E402
    durable_continuity_status,
)


class AsyncTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeChannel:
    def __init__(self, channel_id: int = 2) -> None:
        self.id = channel_id
        self.sent: list[str] = []
        self.typing_count = 0

    def typing(self) -> AsyncTyping:
        self.typing_count += 1
        return AsyncTyping()

    async def send(self, text: str) -> None:
        self.sent.append(text)


def make_message(
    *,
    content: str = "Evelyn hi",
    guild: object | None = None,
    channel: FakeChannel | None = None,
    author: object | None = None,
    message_id: int = 99,
):
    return SimpleNamespace(
        id=message_id,
        content=content,
        guild=guild,
        channel=channel or FakeChannel(),
        author=author or SimpleNamespace(id=3, bot=False, display_name="정훈"),
        attachments=[],
        reference=None,
    )


def make_deps(calls: list[tuple[str, object]], **overrides) -> DiscordTextMessageHandlerDeps:
    async def process_commands(message) -> None:
        calls.append(("process_commands", getattr(message, "content", "")))

    async def stream_text_reply(*args, **kwargs):
        calls.append(("stream", kwargs["user_text"] if "user_text" in kwargs else args[1]))
        metrics = {"meta": {}}
        before_delivery = kwargs.get("before_text_delivery")
        if before_delivery is not None:
            await before_delivery(
                answer_text="<voice>answer</voice>",
                final_text="answer",
                metrics=metrics,
            )
        after_delivery = kwargs.get("after_text_delivery")
        if after_delivery is not None:
            await after_delivery(
                sent_message=SimpleNamespace(id=77),
                final_text="answer",
                metrics=metrics,
            )
        return "<voice>answer</voice>", None, metrics, None

    async def commit_session_continuity(*args, **kwargs):
        calls.append(("commit_continuity", args))
        before_commit = kwargs.get("before_commit")
        if before_commit is not None:
            before_commit(5)
        return durable_continuity_status(5)

    deps = dict(
        process_commands=process_commands,
        bot_user=SimpleNamespace(id=10),
        is_thread_parent=lambda _parent: True,
        remember_session_followup_target=lambda session_key, **kwargs: calls.append(("remember", (session_key, kwargs))),
        get_guild_command_prefix=lambda _guild_id: "!",
        get_guild_command_only_channel_ids=lambda _guild_id: set(),
        contains_wake_word=lambda text: str(text).lower().startswith("evelyn"),
        is_session_active_for_user=lambda _session_key, _user_id: False,
        strip_voice_wake_word=lambda text: str(text).replace("Evelyn", "", 1).strip(),
        empty_wake_text="empty wake",
        log_turn_event=lambda *args, **kwargs: calls.append(("log_turn", kwargs.get("reason"))),
        current_turn_id=lambda session_key: f"current:{session_key}",
        resolve_pending_proactive_question_for_turn=lambda *args, **kwargs: {"resolved": False},
        claim_conversation_ingress=lambda ingress, _text: {
            "entryId": f"entry:{ingress.message_id}",
            "turnId": f"turn:{ingress.session_key}",
            "phase": "accepted",
            "shouldProcess": True,
        },
        conversation_ingress_recovery_context=lambda scope, **_kwargs: {
            "schema": "conversation.ingress-recovery-context.v1",
            "surface": "discord_text",
            "scope": scope,
            "pendingCount": 0,
            "records": [],
            "automaticReplay": False,
        },
        mark_ingress_response_ready=lambda *args, **kwargs: calls.append(
            ("ingress_response_ready", args[0])
        ),
        mark_ingress_delivery_inflight=lambda *args, **kwargs: calls.append(
            ("ingress_delivery_inflight", args[0])
        ),
        mark_ingress_delivery_succeeded=lambda *args, **kwargs: calls.append(
            ("ingress_delivery_succeeded", args[0])
        ),
        mark_ingress_delivery_ambiguous=lambda *args, **kwargs: calls.append(
            ("ingress_delivery_ambiguous", args[0])
        ),
        begin_ingress_terminal_commit=lambda *args, **kwargs: calls.append(
            ("ingress_terminal", args[0])
        ),
        complete_ingress=lambda *args, **kwargs: calls.append(
            ("ingress_complete", args[0])
        ),
        session_locks={},
        reply_slot_locks={},
        reply_slot_admission_locks={},
        begin_user_text_turn=lambda session_key, user_text, **kwargs: SimpleNamespace(
            topic_id=f"topic:{user_text}",
            turn_id=(kwargs.get("turn_id") or f"turn:{session_key}"),
        ),
        replace_room_turn_scope=lambda session_key, turn_scope: calls.append(("replace_scope", session_key)),
        attach_current_task=lambda turn_scope: "task",
        auto_join_voice=False,
        ensure_voice_client=lambda message: None,
        stream_text_reply=stream_text_reply,
        strip_omnivoice_tags=lambda text: text.replace("<voice>", "").replace("</voice>", ""),
        execute_voice_delivery_plan=lambda *args, **kwargs: None,
        detach_task=lambda turn_scope, task: calls.append(("detach", task)),
        clear_room_turn_scope=lambda session_key, turn_scope: calls.append(("clear_scope", session_key)),
        session_speculative_policies={},
        compute_runtime_mode=lambda metrics: "normal",
        record_context_pipeline_benchmark=lambda **kwargs: calls.append(("benchmark", kwargs["answer"])),
        schedule_memory_update=lambda *args, **kwargs: {"scheduled": True},
        should_force_search_followup=lambda *args, **kwargs: False,
        schedule_search_followup=lambda *args, **kwargs: calls.append(("search_followup", kwargs["source"])),
        session_state_snapshot=lambda session_key: {"awaiting_user_reply": False},
        finish_assistant_text_turn=lambda session_key, user_text, answer, **kwargs: calls.append(
            ("finish", (session_key, user_text, answer, kwargs["topic_id"]))
        ),
        commit_session_continuity=commit_session_continuity,
        log_voice_bottleneck_summary=lambda metrics, **kwargs: calls.append(("summary", kwargs["event_name"])),
        record_runtime_error=lambda *_args, **_kwargs: None,
        format_display_text=lambda text, **kwargs: text,
        log=lambda *args: calls.append(("log", args)),
    )
    deps.update(overrides)
    return DiscordTextMessageHandlerDeps(**deps)


class DiscordTextTurnHandlerTests(unittest.TestCase):
    def test_handler_ignores_bot_and_processes_dm_commands(self) -> None:
        calls: list[tuple[str, object]] = []
        deps = make_deps(calls)

        bot_message = make_message(author=SimpleNamespace(id=3, bot=True, display_name="Bot"))
        dm_message = make_message(guild=None)

        asyncio.run(handle_discord_text_message(bot_message, deps))
        asyncio.run(handle_discord_text_message(dm_message, deps))

        self.assertEqual(calls, [("process_commands", "Evelyn hi")])

    def test_prefixed_command_does_not_preseed_followup_target(self) -> None:
        calls: list[tuple[str, object]] = []
        deps = make_deps(calls)
        message = make_message(content="!status", guild=SimpleNamespace(id=1, name="Guild"))

        asyncio.run(handle_discord_text_message(message, deps))

        self.assertFalse(any(name == "remember" for name, _ in calls))
        self.assertEqual(calls[-1], ("process_commands", "!status"))

    def test_prefixed_command_waits_for_exact_session_state(self) -> None:
        calls: list[tuple[str, object]] = []

        async def scenario() -> None:
            deps = make_deps(calls)
            session_key = "guild:1:text:2:user:3"
            state_lock = deps.session_locks.setdefault(
                session_key,
                asyncio.Lock(),
            )
            await state_lock.acquire()
            task = asyncio.create_task(
                handle_discord_text_message(
                    make_message(
                        content="!status",
                        guild=SimpleNamespace(id=1, name="Guild"),
                    ),
                    deps,
                )
            )
            await asyncio.sleep(0)
            reply_lock = deps.reply_slot_locks[
                "guild:1:reply:text:2"
            ]
            self.assertTrue(reply_lock.locked())
            self.assertFalse(any(
                name == "process_commands" for name, _value in calls
            ))
            state_lock.release()
            await task
            self.assertEqual(calls, [("process_commands", "!status")])
            self.assertFalse(reply_lock.locked())
            self.assertFalse(state_lock.locked())

        asyncio.run(scenario())

    def test_prefixed_command_waits_for_inflight_failure_reply(self) -> None:
        calls: list[tuple[str, object]] = []

        async def scenario() -> None:
            started = asyncio.Event()
            release = asyncio.Event()
            channel = FakeChannel()

            async def held_stream(_channel, _user_text, **_kwargs):
                started.set()
                await release.wait()
                raise RuntimeError("generation_failed")

            async def process_commands(message) -> None:
                if str(message.content).startswith("!"):
                    calls.append(("command", message.content))
                    await message.channel.send("new command reply")

            def fail_observer(*_args, **_kwargs) -> None:
                raise RuntimeError("observer_failed")

            deps = make_deps(
                calls,
                process_commands=process_commands,
                stream_text_reply=held_stream,
                log=fail_observer,
                log_voice_bottleneck_summary=fail_observer,
                record_runtime_error=fail_observer,
            )
            guild = SimpleNamespace(id=1, name="Guild")
            normal = asyncio.create_task(
                handle_discord_text_message(
                    make_message(
                        content="Evelyn slow",
                        guild=guild,
                        channel=channel,
                        message_id=101,
                    ),
                    deps,
                )
            )
            await started.wait()
            command = asyncio.create_task(
                handle_discord_text_message(
                    make_message(
                        content="!status",
                        guild=guild,
                        channel=channel,
                        message_id=102,
                    ),
                    deps,
                )
            )
            await asyncio.sleep(0)
            self.assertFalse(command.done())
            release.set()
            await asyncio.gather(normal, command)
            self.assertEqual(len(channel.sent), 2)
            self.assertTrue(channel.sent[0].endswith("(text_turn_failed)"))
            self.assertEqual(channel.sent[1], "new command reply")
            self.assertEqual(
                [
                    name
                    for name, _value in calls
                    if name in {"finish", "commit_continuity", "command"}
                ],
                ["finish", "commit_continuity", "command"],
            )

        asyncio.run(scenario())

    def test_summary_observer_does_not_mask_text_turn_cancellation(self) -> None:
        calls: list[tuple[str, object]] = []

        async def scenario() -> None:
            delivery_started = asyncio.Event()

            async def block_stream(_channel, _user_text, **kwargs):
                await kwargs["before_text_delivery"](
                    answer_text="answer",
                    final_text="answer",
                    metrics={"meta": {}},
                )
                delivery_started.set()
                await asyncio.Event().wait()

            def fail_summary(*_args, **_kwargs) -> None:
                raise RuntimeError("summary_failed")

            deps = make_deps(
                calls,
                stream_text_reply=block_stream,
                log_voice_bottleneck_summary=fail_summary,
            )
            task = asyncio.create_task(
                handle_discord_text_message(
                    make_message(guild=SimpleNamespace(id=1, name="Guild")),
                    deps,
                )
            )
            await delivery_started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(next(iter(deps.reply_slot_locks.values())).locked())

        asyncio.run(scenario())

    def test_handler_runs_full_text_turn_and_records_side_effects(self) -> None:
        calls: list[tuple[str, object]] = []
        deps = make_deps(calls)
        message = make_message(guild=SimpleNamespace(id=1, name="Guild"))

        asyncio.run(handle_discord_text_message(message, deps))

        self.assertIn(("stream", "hi"), calls)
        self.assertIn(("benchmark", "answer"), calls)
        self.assertIn(("search_followup", "search-followup-text"), calls)
        self.assertIn(
            (
                "finish",
                ("guild:1:text:2:user:3", "hi", "answer", "topic:hi"),
            ),
            calls,
        )
        self.assertIn(
            (
                "commit_continuity",
                (
                    "guild:1:text:2:user:3",
                    "turn:guild:1:text:2:user:3",
                ),
            ),
            calls,
        )
        self.assertIn(("summary", "text_turn_summary"), calls)
        self.assertEqual(calls[-1], ("process_commands", "Evelyn hi"))
        self.assertEqual(message.channel.typing_count, 1)

    def test_expired_awaiting_session_does_not_admit_ambient_text(self) -> None:
        calls: list[tuple[str, object]] = []
        store = SessionStateStore.create_empty()
        session_key = "guild:1:text:2:user:3"
        store.mark_active(
            session_key,
            user_id=3,
            ttl_sec=120.0,
            awaiting_user_reply=True,
            active_conversation_awaiting_reply_sec=120.0,
            now_monotonic=100.0,
        )
        deps = make_deps(
            calls,
            is_session_active_for_user=lambda key, user_id: (
                store.is_active_for_user(
                    key,
                    user_id,
                    now_monotonic=221.0,
                )
            ),
        )
        message = make_message(
            content="ambient message",
            guild=SimpleNamespace(id=1, name="Guild"),
        )

        asyncio.run(handle_discord_text_message(message, deps))

        self.assertNotIn("stream", [name for name, _value in calls])
        self.assertIn(("log_turn", "text_gate_not_open"), calls)
        self.assertEqual(calls[-1], ("process_commands", "ambient message"))

    def test_explicit_memory_confirmation_bypasses_llm_and_keeps_continuity(self) -> None:
        calls: list[tuple[str, object]] = []
        summaries: list[dict] = []
        response_receipts: list[tuple[str, object]] = []
        receipt = {
            "schema": "memory.user-confirmation.v1",
            "state": "stored",
            "noteId": "concept-0123456789abcdef",
            "sourceRef": (
                "turn:opaque-turn-" + ("a" * 64) + ":user"
            ),
            "confirmedAt": "2026-07-31T00:00:00+00:00",
            "contentFree": True,
        }

        def unexpected(*_args, **_kwargs):
            raise AssertionError("normal memory path must be skipped")

        deps = make_deps(
            calls,
            auto_join_voice=True,
            ensure_voice_client=unexpected,
            stream_text_reply=unexpected,
            record_context_pipeline_benchmark=unexpected,
            schedule_memory_update=unexpected,
            should_force_search_followup=unexpected,
            schedule_search_followup=unexpected,
            log_voice_bottleneck_summary=(
                lambda metrics, **_kwargs: summaries.append(
                    metrics
                )
            ),
            mark_ingress_response_ready=(
                lambda *_args, **kwargs: response_receipts.append(
                    ("ready", kwargs["memory_receipt_ref"])
                )
            ),
            finish_assistant_text_turn=(
                lambda *_args, **kwargs: response_receipts.append(
                    ("history", kwargs["memory_receipt"])
                )
            ),
            begin_ingress_terminal_commit=(
                lambda *_args, **kwargs: response_receipts.append(
                    ("terminal", kwargs["memory_receipt_ref"])
                )
            ),
            complete_ingress=(
                lambda *_args, **kwargs: response_receipts.append(
                    ("complete", kwargs["memory_receipt_ref"])
                )
            ),
        )
        message = make_message(
            content=(
                "Evelyn /remember 나는 비 오는 날 산책을 좋아해"
            ),
            guild=SimpleNamespace(id=1, name="Guild"),
            message_id=99,
        )

        with patch(
            "evelyn_core.discord_text_turn."
            "execute_explicit_memory_confirmation",
            return_value=(
                True,
                "지금 요청을 근거로 새 기억에 저장했어.",
                receipt,
                "",
            ),
        ) as execute:
            asyncio.run(handle_discord_text_message(message, deps))

        self.assertEqual(
            message.channel.sent,
            ["지금 요청을 근거로 새 기억에 저장했어."],
        )
        self.assertFalse(any(call[0] == "stream" for call in calls))
        self.assertTrue(
            any(
                call
                == (
                    "commit_continuity",
                    (
                        "guild:1:text:2:user:3",
                        "turn:guild:1:text:2:user:3",
                    ),
                )
                for call in calls
            )
        )
        self.assertEqual(
            summaries[0]["meta"]["memory_write_receipt"],
            receipt,
        )
        self.assertEqual(
            summaries[0]["meta"]["memory_writer_decision"][
                "reason"
            ],
            "explicit_user_confirmation",
        )
        expected_memory_ref = not_used_memory_receipt_ref()
        self.assertEqual(
            response_receipts,
            [
                ("ready", expected_memory_ref),
                ("history", expected_memory_ref),
                ("terminal", expected_memory_ref),
                ("complete", expected_memory_ref),
            ],
        )
        execute.assert_called_once_with(
            "/remember 나는 비 오는 날 산책을 좋아해",
            action_id="discord-message:1:2:99",
            evidence_turn_id="turn:guild:1:text:2:user:3",
            source="discord-user",
            owner_scope=memory_owner_scope(
                guild_id=1,
                person_key="user:3",
            ),
        )

    def test_delivered_text_turn_is_committed_when_optional_voice_fails(self) -> None:
        calls: list[tuple[str, object]] = []

        async def stream_text_reply(*args, **kwargs):
            calls.append(("stream", args[1]))
            return (
                "<voice>answer</voice>",
                SimpleNamespace(id=77),
                {"meta": {}},
                SimpleNamespace(should_play_voice=True),
            )

        async def fail_voice(*args, **kwargs):
            calls.append(("voice", kwargs["turn_id"]))
            raise RuntimeError(
                "Bearer discord-secret C:\\Users\\Admin\\private.txt"
            )

        async def ensure_voice_client(_message):
            return SimpleNamespace(is_connected=lambda: True)

        deps = make_deps(
            calls,
            auto_join_voice=True,
            ensure_voice_client=ensure_voice_client,
            stream_text_reply=stream_text_reply,
            execute_voice_delivery_plan=fail_voice,
        )
        message = make_message(
            guild=SimpleNamespace(id=1, name="Guild")
        )

        asyncio.run(handle_discord_text_message(message, deps))

        finish_indexes = [
            index
            for index, call in enumerate(calls)
            if call[0] == "finish"
        ]
        voice_index = next(
            index
            for index, call in enumerate(calls)
            if call[0] == "voice"
        )
        commit_index = next(
            index
            for index, call in enumerate(calls)
            if call[0] == "commit_continuity"
        )
        self.assertEqual(len(finish_indexes), 1)
        self.assertLess(finish_indexes[0], commit_index)
        self.assertLess(commit_index, voice_index)
        self.assertEqual(message.channel.sent, [])
        self.assertNotIn(
            "discord-secret",
            " ".join(
                str(call)
                for call in calls
                if call[0] != "log"
            ),
        )
        self.assertIn(("summary", "text_turn_summary"), calls)
        self.assertIn(
            (
                "remember",
                (
                    "guild:1:text:2:user:3",
                    {"channel_id": 2, "message_id": 99},
                ),
            ),
            calls,
        )
        self.assertEqual(
            calls[-1],
            ("process_commands", "Evelyn hi"),
        )

    def test_partial_commit_status_is_not_marked_durable(
        self,
    ) -> None:
        calls: list[tuple[str, object]] = []
        summaries: list[dict] = []
        private = (
            "Bearer text-continuity-secret "
            "https://internal.example/private"
        )

        async def partial_commit(*_args):
            calls.append(("commit_continuity", None))
            return {
                "state": "ready",
                "rollbackProtected": True,
                "privateMessage": private,
            }

        deps = make_deps(
            calls,
            commit_session_continuity=partial_commit,
            log_voice_bottleneck_summary=(
                lambda metrics, **_kwargs: summaries.append(
                    metrics
                )
            ),
        )
        message = make_message(
            guild=SimpleNamespace(id=1, name="Guild")
        )

        asyncio.run(handle_discord_text_message(message, deps))

        self.assertEqual(
            summaries[0]["meta"]["continuity_commit"],
            "failed",
        )
        self.assertEqual(
            summaries[0]["meta"]["continuity_error"],
            "conversation_continuity_commit_failed",
        )
        self.assertNotIn(private, str(summaries))

    def test_pre_delivery_failure_returns_fixed_public_message(self) -> None:
        calls: list[tuple[str, object]] = []
        summaries: list[dict] = []
        runtime_status = DiscordRuntimeStatus(
            gateway_ready=lambda: True,
            bot_guilds=lambda: [],
            voice_client_type=object,
            now=lambda: 123.0,
        )

        async def fail_stream(*args, **kwargs):
            raise RuntimeError(
                "token=discord-secret http://internal:9820 C:\\private"
            )

        channel = FakeChannel()
        original_send = channel.send

        async def tracked_send(text: str) -> None:
            calls.append(("failure_reply_send", text))
            await original_send(text)

        channel.send = tracked_send  # type: ignore[method-assign]
        deps = make_deps(
            calls,
            stream_text_reply=fail_stream,
            record_runtime_error=runtime_status.record_error,
            log_voice_bottleneck_summary=(
                lambda metrics, **_kwargs: summaries.append(
                    dict(metrics)
                )
            ),
        )
        message = make_message(
            guild=SimpleNamespace(id=1, name="Guild"),
            channel=channel,
        )

        asyncio.run(handle_discord_text_message(message, deps))

        failure_reply = (
            "❌ 응답을 전달하지 못했어. 잠깐 뒤에 다시 시도해줘. "
            "(text_turn_failed)"
        )
        self.assertEqual(
            message.channel.sent,
            [failure_reply],
        )
        self.assertNotIn("discord-secret", message.channel.sent[0])
        self.assertIn(
            (
                "finish",
                (
                    "guild:1:text:2:user:3",
                    "hi",
                    failure_reply,
                    "topic:hi",
                ),
            ),
            calls,
        )
        send_index = next(
            index
            for index, call in enumerate(calls)
            if call[0] == "failure_reply_send"
        )
        finish_index = next(
            index
            for index, call in enumerate(calls)
            if call[0] == "finish"
        )
        commit_index = next(
            index
            for index, call in enumerate(calls)
            if call[0] == "commit_continuity"
        )
        self.assertLess(send_index, finish_index)
        self.assertLess(finish_index, commit_index)
        self.assertTrue(
            summaries[0]["meta"]["failure_reply_delivered"]
        )
        self.assertEqual(
            summaries[0]["meta"]["continuity_commit"],
            "durable",
        )
        status = runtime_status.snapshot()
        self.assertEqual(status["lastErrorCode"], "discord_text_turn_failed")
        self.assertEqual(status["lastErrorType"], "RuntimeError")
        self.assertEqual(status["errorCount"], 1)
        self.assertNotIn("discord-secret", str(status))

    def test_failed_fallback_delivery_does_not_mutate_continuity(
        self,
    ) -> None:
        calls: list[tuple[str, object]] = []
        secret = (
            "Bearer discord-fallback-secret "
            r"C:\Users\Admin\private.txt"
        )

        async def fail_stream(*_args, **_kwargs):
            raise RuntimeError("generation_failed")

        class FailingChannel(FakeChannel):
            async def send(self, _text: str) -> None:
                raise RuntimeError(secret)

        deps = make_deps(
            calls,
            stream_text_reply=fail_stream,
        )
        message = make_message(
            guild=SimpleNamespace(id=1, name="Guild"),
            channel=FailingChannel(),
        )

        asyncio.run(handle_discord_text_message(message, deps))

        self.assertFalse(
            any(
                call[0] in {"finish", "commit_continuity"}
                for call in calls
            )
        )
        rendered_logs = " ".join(
            str(call)
            for call in calls
            if call[0] == "log"
        )
        self.assertIn(
            "failure_reply_delivery_failed",
            rendered_logs,
        )
        self.assertIn("RuntimeError", rendered_logs)
        self.assertNotIn("discord-fallback-secret", rendered_logs)
        self.assertNotIn("Users", rendered_logs)

    def test_delivered_failure_contains_record_error_without_retry(
        self,
    ) -> None:
        calls: list[tuple[str, object]] = []
        secret = (
            "Bearer failure-record-secret "
            r"C:\Users\Admin\checkpoint.json"
        )

        async def fail_stream(*_args, **_kwargs):
            raise RuntimeError("generation_failed")

        def fail_finish(*_args, **_kwargs):
            raise RuntimeError(secret)

        deps = make_deps(
            calls,
            stream_text_reply=fail_stream,
            finish_assistant_text_turn=fail_finish,
        )
        message = make_message(
            guild=SimpleNamespace(id=1, name="Guild")
        )

        asyncio.run(handle_discord_text_message(message, deps))

        self.assertEqual(len(message.channel.sent), 1)
        self.assertFalse(
            any(call[0] == "commit_continuity" for call in calls)
        )
        rendered_logs = " ".join(
            str(call)
            for call in calls
            if call[0] == "log"
        )
        self.assertIn(
            "failure_turn_record_failed",
            rendered_logs,
        )
        self.assertIn("RuntimeError", rendered_logs)
        self.assertNotIn("failure-record-secret", rendered_logs)
        self.assertNotIn("checkpoint.json", rendered_logs)


class DiscordTextIngressRecoveryIntegrationTests(unittest.TestCase):
    @staticmethod
    def owner(
        root: Path,
        *,
        reconcile=None,
        verify=None,
    ) -> ConversationIngressComposition:
        owner = ConversationIngressComposition(
            ConversationIngressCompositionDeps(
                journal_factory=lambda: ConversationIngressRecoveryJournal(
                    path=root / "main.json",
                    head_path=root / "main.head.json",
                ),
                log=lambda *_args: None,
                reconcile_delivery_succeeded=reconcile,
                verify_terminal_commit=verify,
            )
        )
        owner.activate_after_continuity_restore()
        return owner

    @staticmethod
    def ingress_overrides(owner: ConversationIngressComposition):
        return {
            "claim_conversation_ingress": owner.claim_discord_text,
            "conversation_ingress_recovery_context": (
                owner.recovery_context_for_scope
            ),
            "mark_ingress_response_ready": owner.mark_response_ready,
            "mark_ingress_delivery_inflight": owner.mark_delivery_inflight,
            "mark_ingress_delivery_succeeded": owner.mark_delivery_succeeded,
            "mark_ingress_delivery_ambiguous": owner.mark_delivery_ambiguous,
            "begin_ingress_terminal_commit": owner.begin_terminal_commit,
            "complete_ingress": owner.complete,
        }

    def test_completed_gateway_redelivery_runs_no_downstream_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_owner = self.owner(root)
            first_calls: list[tuple[str, object]] = []
            message = make_message(guild=SimpleNamespace(id=1, name="Guild"))
            first_deps = make_deps(
                first_calls,
                **self.ingress_overrides(first_owner),
            )

            asyncio.run(handle_discord_text_message(message, first_deps))
            self.assertEqual(
                first_owner.public_status()["phases"]["completed"],
                1,
            )

            restarted_owner = self.owner(root)
            restarted_calls: list[tuple[str, object]] = []
            restarted_deps = make_deps(
                restarted_calls,
                **self.ingress_overrides(restarted_owner),
            )
            asyncio.run(
                handle_discord_text_message(message, restarted_deps)
            )

        forbidden = {
            "stream",
            "finish",
            "commit_continuity",
            "benchmark",
            "search_followup",
            "process_commands",
        }
        self.assertFalse(
            any(call[0] in forbidden for call in restarted_calls)
        )

    def test_pending_restart_and_changed_binding_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_owner = self.owner(root)
            ingress = build_text_ingress_context(
                guild_id=1,
                channel_id=2,
                user_id=3,
                message_id=99,
            )
            first_owner.claim_discord_text(ingress, "hi")

            restarted_owner = self.owner(root)
            same_calls: list[tuple[str, object]] = []
            same_message = make_message(
                guild=SimpleNamespace(id=1, name="Guild")
            )
            asyncio.run(
                handle_discord_text_message(
                    same_message,
                    make_deps(
                        same_calls,
                        **self.ingress_overrides(restarted_owner),
                    ),
                )
            )
            changed_calls: list[tuple[str, object]] = []
            changed_message = make_message(
                content="Evelyn changed",
                guild=SimpleNamespace(id=1, name="Guild"),
            )
            asyncio.run(
                handle_discord_text_message(
                    changed_message,
                    make_deps(
                        changed_calls,
                        **self.ingress_overrides(restarted_owner),
                    ),
                )
            )

        for calls in (same_calls, changed_calls):
            self.assertFalse(any(call[0] == "stream" for call in calls))
            self.assertFalse(any(call[0] == "finish" for call in calls))

    def test_missing_message_id_is_fail_closed_before_turn_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            owner = self.owner(Path(tmp))
            calls: list[tuple[str, object]] = []
            message = make_message(
                guild=SimpleNamespace(id=1, name="Guild"),
                message_id=None,
            )
            asyncio.run(
                handle_discord_text_message(
                    message,
                    make_deps(
                        calls,
                        **self.ingress_overrides(owner),
                    ),
                )
            )

        self.assertFalse(any(call[0] == "stream" for call in calls))
        self.assertFalse(any(call[0] == "finish" for call in calls))

    def test_send_side_effect_then_timeout_is_ambiguous_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            owner = self.owner(Path(tmp))
            calls: list[tuple[str, object]] = []
            channel = FakeChannel()

            async def ambiguous_stream(channel, _user_text, **kwargs):
                metrics = {"meta": {}}
                await kwargs["before_text_delivery"](
                    answer_text="answer",
                    final_text="answer",
                    metrics=metrics,
                )
                await channel.send("answer")
                raise TimeoutError("outcome unknown")

            message = make_message(
                guild=SimpleNamespace(id=1, name="Guild"),
                channel=channel,
            )
            asyncio.run(
                handle_discord_text_message(
                    message,
                    make_deps(
                        calls,
                        stream_text_reply=ambiguous_stream,
                        **self.ingress_overrides(owner),
                    ),
                )
            )

            status = owner.public_status()

        self.assertEqual(channel.sent, ["answer"])
        self.assertEqual(status["phases"]["delivery_ambiguous"], 1)
        self.assertEqual(status["phases"]["completed"], 0)
        self.assertFalse(any(call[0] == "finish" for call in calls))

    def test_journal_and_history_bind_exact_discord_final_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            owner = self.owner(Path(tmp))
            calls: list[tuple[str, object]] = []
            channel = FakeChannel()
            claimed: dict[str, str] = {}

            def claim(ingress, accepted_text):
                receipt = owner.claim_discord_text(ingress, accepted_text)
                claimed["entry_id"] = receipt["entryId"]
                return receipt

            async def formatted_stream(channel, _user_text, **kwargs):
                metrics = {"meta": {}}
                await kwargs["before_text_delivery"](
                    answer_text="semantic answer",
                    final_text="[display] exact sent answer",
                    metrics=metrics,
                )
                await channel.send("[display] exact sent answer")
                await kwargs["after_text_delivery"](
                    sent_message=SimpleNamespace(id=77),
                    final_text="[display] exact sent answer",
                    metrics=metrics,
                )
                return "semantic answer", None, metrics, None

            overrides = self.ingress_overrides(owner)
            overrides["claim_conversation_ingress"] = claim
            message = make_message(
                guild=SimpleNamespace(id=1, name="Guild"),
                channel=channel,
            )
            asyncio.run(
                handle_discord_text_message(
                    message,
                    make_deps(
                        calls,
                        stream_text_reply=formatted_stream,
                        **overrides,
                    ),
                )
            )
            record = owner.record_for(claimed["entry_id"])

        self.assertEqual(channel.sent, ["[display] exact sent answer"])
        self.assertEqual(
            record["assistantText"],
            "[display] exact sent answer",
        )
        finish = next(call for call in calls if call[0] == "finish")
        self.assertEqual(
            finish[1][2],
            "[display] exact sent answer",
        )

    def test_continuity_failure_blocks_followups_and_next_same_scope_claim(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            owner = self.owner(Path(tmp))
            calls: list[tuple[str, object]] = []
            channel = FakeChannel()

            async def delivered_stream(channel, _user_text, **kwargs):
                metrics = {"meta": {}}
                await kwargs["before_text_delivery"](
                    answer_text="answer",
                    final_text="answer",
                    metrics=metrics,
                )
                await channel.send("answer")
                await kwargs["after_text_delivery"](
                    sent_message=SimpleNamespace(id=77),
                    final_text="answer",
                    metrics=metrics,
                )
                return (
                    "answer",
                    None,
                    metrics,
                    SimpleNamespace(should_play_voice=True),
                )

            async def failed_commit(*_args, **kwargs):
                kwargs["before_commit"](5)
                raise RuntimeError("checkpoint unavailable")

            async def ensure_voice(_message):
                return SimpleNamespace()

            overrides = self.ingress_overrides(owner)
            deps = make_deps(
                calls,
                stream_text_reply=delivered_stream,
                commit_session_continuity=failed_commit,
                auto_join_voice=True,
                ensure_voice_client=ensure_voice,
                record_context_pipeline_benchmark=lambda **_kwargs: calls.append(
                    ("benchmark_after_failed_commit", None)
                ),
                schedule_memory_update=lambda *_args, **_kwargs: calls.append(
                    ("memory_after_failed_commit", None)
                ),
                schedule_search_followup=lambda *_args, **_kwargs: calls.append(
                    ("search_after_failed_commit", None)
                ),
                execute_voice_delivery_plan=lambda *_args, **_kwargs: calls.append(
                    ("voice_after_failed_commit", None)
                ),
                **overrides,
            )
            first_message = make_message(
                guild=SimpleNamespace(id=1, name="Guild"),
                channel=channel,
                message_id=99,
            )
            asyncio.run(handle_discord_text_message(first_message, deps))
            second_message = make_message(
                content="Evelyn next",
                guild=SimpleNamespace(id=1, name="Guild"),
                channel=channel,
                message_id=100,
            )
            asyncio.run(handle_discord_text_message(second_message, deps))
            status = owner.public_status()

        self.assertEqual(channel.sent, ["answer"])
        self.assertEqual(status["phases"]["terminal_committing"], 1)
        self.assertEqual(status["entryCount"], 1)
        forbidden = {
            "benchmark_after_failed_commit",
            "memory_after_failed_commit",
            "search_after_failed_commit",
            "voice_after_failed_commit",
        }
        self.assertFalse(any(call[0] in forbidden for call in calls))

    def test_concurrent_messages_cannot_start_two_state_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            owner = self.owner(Path(tmp))
            calls: list[tuple[str, object]] = []
            first_started: asyncio.Event
            release_first: asyncio.Event
            begin_count = 0
            base_deps = make_deps(calls, **self.ingress_overrides(owner))
            original_begin = base_deps.begin_user_text_turn

            def begin(*args, **kwargs):
                nonlocal begin_count
                begin_count += 1
                return original_begin(*args, **kwargs)

            async def scenario() -> None:
                nonlocal first_started, release_first
                first_started = asyncio.Event()
                release_first = asyncio.Event()

                async def held_stream(channel, user_text, **kwargs):
                    metrics = {"meta": {}}
                    if user_text == "one":
                        first_started.set()
                        await release_first.wait()
                    await kwargs["before_text_delivery"](
                        answer_text=f"answer:{user_text}",
                        final_text=f"answer:{user_text}",
                        metrics=metrics,
                    )
                    await channel.send(f"answer:{user_text}")
                    await kwargs["after_text_delivery"](
                        sent_message=SimpleNamespace(id=77),
                        final_text=f"answer:{user_text}",
                        metrics=metrics,
                    )
                    return f"answer:{user_text}", None, metrics, None

                deps = make_deps(
                    calls,
                    begin_user_text_turn=begin,
                    stream_text_reply=held_stream,
                    **self.ingress_overrides(owner),
                )
                channel = FakeChannel()
                first = asyncio.create_task(
                    handle_discord_text_message(
                        make_message(
                            content="Evelyn one",
                            guild=SimpleNamespace(id=1, name="Guild"),
                            channel=channel,
                            message_id=101,
                        ),
                        deps,
                    )
                )
                await first_started.wait()
                second = asyncio.create_task(
                    handle_discord_text_message(
                        make_message(
                            content="Evelyn two",
                            guild=SimpleNamespace(id=1, name="Guild"),
                            channel=channel,
                            message_id=102,
                        ),
                        deps,
                    )
                )
                await asyncio.sleep(0)
                release_first.set()
                await asyncio.gather(first, second)

            asyncio.run(scenario())
            status = owner.public_status()

        self.assertEqual(begin_count, 1)
        self.assertEqual(status["entryCount"], 1)

    def test_concurrent_users_share_atomic_reply_slot_admission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            owner = self.owner(Path(tmp))
            calls: list[tuple[str, object]] = []
            claim_count = 0
            begin_count = 0
            count_lock = threading.Lock()
            first_claim_started = threading.Event()
            second_claim_started = threading.Event()
            release_claim = threading.Event()
            base_deps = make_deps(calls)
            original_begin = base_deps.begin_user_text_turn

            def claim(ingress, accepted_text):
                nonlocal claim_count
                with count_lock:
                    claim_count += 1
                    current_count = claim_count
                if current_count == 1:
                    first_claim_started.set()
                elif current_count == 2:
                    second_claim_started.set()
                release_claim.wait(timeout=2.0)
                return owner.claim_discord_text(ingress, accepted_text)

            def begin(*args, **kwargs):
                nonlocal begin_count
                begin_count += 1
                return original_begin(*args, **kwargs)

            async def scenario() -> None:
                ingress_overrides = self.ingress_overrides(owner)
                ingress_overrides[
                    "claim_conversation_ingress"
                ] = claim
                deps = make_deps(
                    calls,
                    begin_user_text_turn=begin,
                    **ingress_overrides,
                )
                channel = FakeChannel()
                first = asyncio.create_task(
                    handle_discord_text_message(
                        make_message(
                            content="Evelyn one",
                            guild=SimpleNamespace(id=1, name="Guild"),
                            channel=channel,
                            author=SimpleNamespace(
                                id=3,
                                bot=False,
                                display_name="User 3",
                            ),
                            message_id=101,
                        ),
                        deps,
                    )
                )
                second = None
                try:
                    started = await asyncio.to_thread(
                        first_claim_started.wait,
                        2.0,
                    )
                    self.assertTrue(started)
                    second = asyncio.create_task(
                        handle_discord_text_message(
                            make_message(
                                content="Evelyn two",
                                guild=SimpleNamespace(id=1, name="Guild"),
                                channel=channel,
                                author=SimpleNamespace(
                                    id=4,
                                    bot=False,
                                    display_name="User 4",
                                ),
                                message_id=102,
                            ),
                            deps,
                        )
                    )
                    await asyncio.to_thread(
                        second_claim_started.wait,
                        0.1,
                    )
                finally:
                    release_claim.set()
                if second is not None:
                    await asyncio.gather(first, second)
                else:
                    await first

            asyncio.run(scenario())
            status = owner.public_status()

        self.assertEqual(claim_count, 1)
        self.assertEqual(begin_count, 1)
        self.assertEqual(status["entryCount"], 1)

    def test_locked_reply_slot_is_rejected_before_durable_claim(self) -> None:
        calls: list[tuple[str, object]] = []
        claim_count = 0

        def claim(*_args, **_kwargs):
            nonlocal claim_count
            claim_count += 1
            raise AssertionError("claim must not run while slot is busy")

        async def scenario() -> FakeChannel:
            lock = asyncio.Lock()
            await lock.acquire()
            channel = FakeChannel()
            deps = make_deps(
                calls,
                claim_conversation_ingress=claim,
                reply_slot_locks={"guild:1:reply:text:2": lock},
            )
            try:
                await handle_discord_text_message(
                    make_message(
                        guild=SimpleNamespace(id=1, name="Guild"),
                        channel=channel,
                    ),
                    deps,
                )
            finally:
                lock.release()
            return channel

        channel = asyncio.run(scenario())
        self.assertEqual(claim_count, 0)
        self.assertEqual(channel.sent, [])
        self.assertFalse(any(name == "remember" for name, _ in calls))

    def test_restart_reconciles_critical_phases_or_disables_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            owner = self.owner(root)
            ingress = build_text_ingress_context(
                guild_id=1,
                channel_id=2,
                user_id=3,
                message_id=99,
            )
            claim = owner.claim_discord_text(ingress, "hi")
            memory_ref = unattributed_memory_receipt_ref()
            owner.mark_response_ready(
                claim["entryId"],
                assistant_text="answer",
                memory_receipt_ref=memory_ref,
            )
            owner.mark_delivery_inflight(
                claim["entryId"],
                delivery_ref="delivery-1",
            )
            owner.mark_delivery_succeeded(
                claim["entryId"],
                delivery_ref="delivery-1",
            )

            blocked_owner = self.owner(root, reconcile=lambda _record: None)
            self.assertFalse(blocked_owner.public_status()["ownerReady"])

            reconciled_owner = self.owner(root, reconcile=lambda _record: 7)
            status = reconciled_owner.public_status()

        self.assertTrue(status["ownerReady"])
        self.assertEqual(status["phases"]["completed"], 1)
        self.assertEqual(status["reconciledRecoveryCount"], 1)


if __name__ == "__main__":
    unittest.main()
