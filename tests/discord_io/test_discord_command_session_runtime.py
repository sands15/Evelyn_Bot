import unittest
import sys
import asyncio
import tempfile
from functools import partial
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_command_session_runtime import (
    ContinuityRecordingCommandContext,
    DiscordCommandSessionRuntimeDeps,
    mark_text_session_from_command_runtime,
)
from evelyn_core.conversation_ingress_composition import (
    ConversationIngressComposition,
    ConversationIngressCompositionDeps,
)
from evelyn_core.conversation_ingress_recovery import (
    ConversationIngressRecoveryJournal,
    conversation_ingress_entry_id,
)
from evelyn_core.conversation_ingress_restart_runtime import (
    ConversationIngressRestartDeps,
    reconcile_recovered_delivery_succeeded,
    reconcile_recovered_terminal_commit,
)
from evelyn_core.session_continuity import SessionContinuityCheckpoint
from evelyn_core.session_memory_state import SessionStateStore
from tests.continuity_test_support import (
    durable_continuity_status,
)


class DiscordCommandSessionRuntimeTests(unittest.TestCase):
    @staticmethod
    def _real_owner(root: Path, logs: list[str]):
        store = SessionStateStore.create_empty()
        continuity_root = root / "continuity"
        checkpoint = SessionContinuityCheckpoint(
            store=store,
            checkpoint_path=continuity_root / "active.json",
            status_path=continuity_root / "status.json",
            system_prompt="system",
            max_history_items=12,
        )
        checkpoint.restore()
        restart_deps = ConversationIngressRestartDeps(
            session_state_store=store,
            session_continuity_checkpoint=checkpoint,
            system_prompt="system",
            max_history_items=12,
            normal_ttl_sec=30.0,
            question_ttl_sec=45.0,
            log=lambda *parts: logs.append(" ".join(map(str, parts))),
        )
        ingress_root = root / "conversation_ingress"
        owner = ConversationIngressComposition(
            ConversationIngressCompositionDeps(
                journal_factory=lambda: ConversationIngressRecoveryJournal(
                    path=ingress_root / "main.json",
                    head_path=ingress_root / "main.head.json",
                ),
                log=lambda *parts: logs.append(" ".join(map(str, parts))),
                active_guild_revocation_ids=(
                    checkpoint.active_guild_revocation_ids
                ),
                reset_session_continuity_guild=checkpoint.reset_guild,
                reset_guild_persistent_memory=lambda _guild_id: None,
                reconcile_delivery_succeeded=partial(
                    reconcile_recovered_delivery_succeeded,
                    deps=restart_deps,
                ),
                verify_terminal_commit=partial(
                    reconcile_recovered_terminal_commit,
                    deps=restart_deps,
                ),
            )
        )
        owner.activate_after_continuity_restore()
        return owner, checkpoint, store

    @staticmethod
    def _real_runtime_deps(owner, checkpoint, store, logs, *, commit=None):
        return DiscordCommandSessionRuntimeDeps(
            resolve_text_thread_id=lambda *_args, **_kwargs: None,
            is_text_thread_parent=lambda _parent: False,
            make_text_session_key=(
                lambda guild_id, channel_id, user_id, **_kwargs: (
                    f"guild:{guild_id}:text:{channel_id}:user:{user_id}"
                )
            ),
            start_new_turn=store.start_new_turn,
            record_command_assistant_turn=(
                store.record_command_assistant_turn
            ),
            system_prompt="system",
            max_history_items=12,
            normal_ttl_sec=30.0,
            question_ttl_sec=45.0,
            commit_session_continuity=(
                commit or checkpoint.commit_completed_turn
            ),
            log=lambda *parts, **_kwargs: logs.append(
                " ".join(map(str, parts))
            ),
            conversation_ingress=owner,
        )

    @staticmethod
    def _real_wrapped(ctx, deps, logs):
        return ContinuityRecordingCommandContext(
            ctx,
            record_reply=lambda context, user, answer, **kwargs: (
                mark_text_session_from_command_runtime(
                    context,
                    user,
                    answer,
                    deps=deps,
                    **kwargs,
                )
            ),
            log=lambda *parts, **_kwargs: logs.append(
                " ".join(map(str, parts))
            ),
            runtime_deps=deps,
        )

    @staticmethod
    def _journaled_runtime_deps(ingress, order, *, commit=None):
        if commit is None:
            def commit(*_args, before_commit=None, **_kwargs):
                order.append("commit")
                if before_commit is not None:
                    before_commit(7)
                return durable_continuity_status(7)

        return DiscordCommandSessionRuntimeDeps(
            resolve_text_thread_id=lambda *_args, **_kwargs: None,
            is_text_thread_parent=lambda _parent: False,
            make_text_session_key=(
                lambda guild_id, channel_id, user_id, **_kwargs: (
                    f"guild:{guild_id}:text:{channel_id}:user:{user_id}"
                )
            ),
            start_new_turn=lambda session_key, *, turn_id=None: (
                order.append(("start", session_key, turn_id))
                or turn_id
                or "legacy-turn"
            ),
            record_command_assistant_turn=(
                lambda *_args, **_kwargs: order.append("record")
            ),
            system_prompt="system",
            max_history_items=12,
            normal_ttl_sec=30.0,
            question_ttl_sec=45.0,
            commit_session_continuity=commit,
            log=lambda *_args, **_kwargs: None,
            conversation_ingress=ingress,
        )

    @staticmethod
    def _journaled_context(order, *, message_id=4):
        class Context:
            guild = SimpleNamespace(id=1)
            channel = SimpleNamespace(id=2)
            author = SimpleNamespace(id=3)
            message = SimpleNamespace(id=message_id, content="!상태")

            async def send(self, content=None, *args, **kwargs):
                order.append(("send", content))
                return f"sent:{content}"

        return Context()

    def test_journaled_context_uses_reply_ordinal_and_terminal_order(self) -> None:
        order: list[object] = []

        class Ingress:
            def guild_epoch(self, guild_id):
                return 5

            def claim_discord_command(self, **kwargs):
                order.append(("claim", kwargs["source_delivery_id"]))
                ordinal = kwargs["source_delivery_id"].rsplit(":", 1)[-1]
                return {
                    "entryId": f"entry-{ordinal}",
                    "turnId": f"turn-{ordinal}",
                    "guildEpoch": 5,
                    "shouldProcess": True,
                }

            def bind_response(self, entry_id, **_kwargs):
                order.append(("bind", entry_id))
                return {"assistantHash": f"hash-{entry_id}"}

            def mark_delivery_inflight(self, entry_id, **_kwargs):
                order.append(("inflight", entry_id))

            def mark_delivery_succeeded(self, entry_id, **_kwargs):
                order.append(("succeeded", entry_id))

            def begin_terminal_commit(self, entry_id, **_kwargs):
                order.append(("terminal", entry_id))

            def complete(self, entry_id, **_kwargs):
                order.append(("complete", entry_id))

        ingress = Ingress()
        deps = self._journaled_runtime_deps(ingress, order)
        ctx = self._journaled_context(order)
        wrapped = ContinuityRecordingCommandContext(
            ctx,
            record_reply=lambda context, user, answer, **kwargs: (
                mark_text_session_from_command_runtime(
                    context,
                    user,
                    answer,
                    deps=deps,
                    **kwargs,
                )
            ),
            log=lambda *_args, **_kwargs: None,
            runtime_deps=deps,
        )

        self.assertEqual(asyncio.run(wrapped.send("one")), "sent:one")
        self.assertEqual(asyncio.run(wrapped.send("two")), "sent:two")

        self.assertEqual(
            [item for item in order if isinstance(item, tuple) and item[0] == "claim"],
            [("claim", "command:4:0"), ("claim", "command:4:1")],
        )
        for ordinal in (0, 1):
            entry = f"entry-{ordinal}"
            positions = {
                name: order.index((name, entry))
                for name in ("bind", "inflight", "succeeded", "terminal", "complete")
            }
            self.assertLess(positions["bind"], positions["inflight"])
            self.assertLess(positions["inflight"], order.index(("send", ("one", "two")[ordinal])))
            self.assertLess(order.index(("send", ("one", "two")[ordinal])), positions["succeeded"])
            self.assertLess(positions["succeeded"], positions["terminal"])
            self.assertLess(positions["terminal"], positions["complete"])

    def test_commit_failure_returns_physical_result_without_resend(self) -> None:
        order: list[object] = []
        logs: list[str] = []

        class Ingress:
            def guild_epoch(self, _guild_id):
                return 0

            def claim_discord_command(self, **_kwargs):
                return {
                    "entryId": "entry",
                    "turnId": "turn",
                    "guildEpoch": 0,
                    "shouldProcess": True,
                }

            def bind_response(self, *_args, **_kwargs):
                return {"assistantHash": "a" * 64}

            def mark_delivery_inflight(self, *_args, **_kwargs):
                return None

            def mark_delivery_succeeded(self, *_args, **_kwargs):
                order.append("delivery_succeeded")

            def begin_terminal_commit(self, *_args, **_kwargs):
                order.append("terminal")

            def complete(self, *_args, **_kwargs):
                order.append("complete")

        def fail_commit(*_args, **_kwargs):
            raise RuntimeError("private commit detail")

        ingress = Ingress()
        deps = self._journaled_runtime_deps(
            ingress,
            order,
            commit=fail_commit,
        )
        ctx = self._journaled_context(order)
        wrapped = ContinuityRecordingCommandContext(
            ctx,
            record_reply=lambda context, user, answer, **kwargs: (
                mark_text_session_from_command_runtime(
                    context,
                    user,
                    answer,
                    deps=deps,
                    **kwargs,
                )
            ),
            log=lambda *parts, **_kwargs: logs.append(" ".join(map(str, parts))),
            runtime_deps=deps,
        )

        self.assertEqual(asyncio.run(wrapped.send("one")), "sent:one")
        self.assertEqual(order.count(("send", "one")), 1)
        self.assertEqual(order.count("delivery_succeeded"), 1)
        self.assertNotIn("terminal", order)
        self.assertNotIn("complete", order)
        self.assertIn("sent_but_continuity_pending", " ".join(logs))
        self.assertNotIn("private commit detail", " ".join(logs))

    def test_stale_reset_epoch_suppresses_send_until_explicit_refresh(self) -> None:
        order: list[object] = []

        class Ingress:
            epoch = 0

            def guild_epoch(self, _guild_id):
                return self.epoch

            def claim_discord_command(self, *, expected_guild_epoch, **_kwargs):
                if expected_guild_epoch != self.epoch:
                    raise RuntimeError("stale epoch")
                return {
                    "entryId": "entry",
                    "turnId": "turn",
                    "guildEpoch": self.epoch,
                    "shouldProcess": True,
                }

            def bind_response(self, *_args, **_kwargs):
                return {"assistantHash": "a" * 64}

            def mark_delivery_inflight(self, *_args, **_kwargs):
                return None

            def mark_delivery_succeeded(self, *_args, **_kwargs):
                return None

            def begin_terminal_commit(self, *_args, **_kwargs):
                return None

            def complete(self, *_args, **_kwargs):
                return None

        ingress = Ingress()
        deps = self._journaled_runtime_deps(ingress, order)
        ctx = self._journaled_context(order)
        wrapped = ContinuityRecordingCommandContext(
            ctx,
            record_reply=lambda context, user, answer, **kwargs: (
                mark_text_session_from_command_runtime(
                    context,
                    user,
                    answer,
                    deps=deps,
                    **kwargs,
                )
            ),
            log=lambda *_args, **_kwargs: None,
            runtime_deps=deps,
        )

        ingress.epoch = 1
        self.assertIsNone(asyncio.run(wrapped.send("stale")))
        self.assertEqual(order.count(("send", "stale")), 0)

        wrapped.refresh_ingress_epoch()
        self.assertEqual(asyncio.run(wrapped.send("reset confirmed")), "sent:reset confirmed")
        self.assertEqual(order.count(("send", "reset confirmed")), 1)

    def test_commit_failure_restarts_into_exact_pair_without_resend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs: list[str] = []
            owner, checkpoint, store = self._real_owner(root, logs)

            def fail_before_terminal(*_args, **_kwargs):
                raise RuntimeError("private commit failure")

            deps = self._real_runtime_deps(
                owner,
                checkpoint,
                store,
                logs,
                commit=fail_before_terminal,
            )
            sends: list[str] = []

            class Context:
                guild = SimpleNamespace(id=1)
                channel = SimpleNamespace(id=2)
                author = SimpleNamespace(id=3)
                message = SimpleNamespace(id=91, content="!상태")

                async def send(self, content=None):
                    sends.append(content)
                    return "discord-message"

            first = self._real_wrapped(Context(), deps, logs)
            self.assertEqual(
                asyncio.run(first.send("delivered answer")),
                "discord-message",
            )
            self.assertEqual(sends, ["delivered answer"])
            self.assertEqual(
                owner.public_status()["phases"]["delivery_succeeded"],
                1,
            )
            self.assertIn("sent_but_continuity_pending", " ".join(logs))
            self.assertNotIn("private commit failure", " ".join(logs))

            recovered_owner, recovered_checkpoint, recovered_store = (
                self._real_owner(root, logs)
            )
            self.assertEqual(
                recovered_owner.public_status()["phases"]["completed"],
                1,
            )
            history = recovered_store.get_conversation_history(
                system_prompt="system",
                session_key="guild:1:text:2:user:3",
            )
            self.assertEqual(
                [(row["role"], row["content"]) for row in history[-2:]],
                [("user", "!상태"), ("assistant", "delivered answer")],
            )
            self.assertFalse(
                recovered_store.awaiting_user_reply[
                    "guild:1:text:2:user:3"
                ]
            )

            replay_deps = self._real_runtime_deps(
                recovered_owner,
                recovered_checkpoint,
                recovered_store,
                logs,
            )
            replay_sends: list[str] = []

            class ReplayContext(Context):
                async def send(self, content=None):
                    replay_sends.append(content)
                    return "duplicate"

            replay = self._real_wrapped(ReplayContext(), replay_deps, logs)
            self.assertIsNone(
                asyncio.run(replay.send("delivered answer"))
            )
            self.assertEqual(replay_sends, [])

    def test_multi_reply_persists_distinct_command_delivery_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs: list[str] = []
            owner, checkpoint, store = self._real_owner(root, logs)
            deps = self._real_runtime_deps(
                owner,
                checkpoint,
                store,
                logs,
            )
            sends: list[str] = []

            class Context:
                guild = SimpleNamespace(id=1)
                channel = SimpleNamespace(id=2)
                author = SimpleNamespace(id=3)
                message = SimpleNamespace(id=96, content="!두번")

                async def send(self, content=None):
                    sends.append(content)
                    return f"discord:{content}"

            wrapped = self._real_wrapped(Context(), deps, logs)
            self.assertEqual(asyncio.run(wrapped.send("first")), "discord:first")
            self.assertEqual(asyncio.run(wrapped.send("second")), "discord:second")

            scope = "guild:1:text:2:user:3"
            records = [
                owner.record_for(
                    conversation_ingress_entry_id(
                        surface="discord_text",
                        scope=scope,
                        source_delivery_id=source_delivery_id,
                    )
                )
                for source_delivery_id in ("command:96:0", "command:96:1")
            ]
            self.assertEqual(
                [record["sourceDeliveryId"] for record in records if record],
                ["command:96:0", "command:96:1"],
            )
            self.assertEqual(
                [record["phase"] for record in records if record],
                ["completed", "completed"],
            )
            history = store.get_conversation_history(
                system_prompt="system",
                session_key=scope,
            )
            self.assertEqual(
                [row["content"] for row in history[-4:]],
                ["!두번", "first", "!두번", "second"],
            )
            self.assertEqual(sends, ["first", "second"])

    def test_terminal_complete_failure_finishes_on_restart_without_recommit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs: list[str] = []
            owner, checkpoint, store = self._real_owner(root, logs)
            deps = self._real_runtime_deps(
                owner,
                checkpoint,
                store,
                logs,
            )

            def fail_complete(*_args, **_kwargs):
                raise RuntimeError("private terminal failure")

            owner.complete = fail_complete  # type: ignore[method-assign]
            sends: list[str] = []

            class Context:
                guild = SimpleNamespace(id=1)
                channel = SimpleNamespace(id=2)
                author = SimpleNamespace(id=3)
                message = SimpleNamespace(id=92, content="!도움")

                async def send(self, content=None):
                    sends.append(content)
                    return "discord-message"

            wrapped = self._real_wrapped(Context(), deps, logs)
            self.assertEqual(
                asyncio.run(wrapped.send("delivered help")),
                "discord-message",
            )
            generation = checkpoint.status()["checkpointGeneration"]
            self.assertGreaterEqual(generation, 1)
            self.assertEqual(
                owner.public_status()["phases"]["terminal_committing"],
                1,
            )

            recovered_owner, recovered_checkpoint, recovered_store = (
                self._real_owner(root, logs)
            )
            self.assertEqual(
                recovered_owner.public_status()["phases"]["completed"],
                1,
            )
            self.assertEqual(
                recovered_checkpoint.status()["checkpointGeneration"],
                generation,
            )
            history = recovered_store.get_conversation_history(
                system_prompt="system",
                session_key="guild:1:text:2:user:3",
            )
            self.assertEqual(
                [(row["role"], row["content"]) for row in history[-2:]],
                [("user", "!도움"), ("assistant", "delivered help")],
            )
            self.assertFalse(
                recovered_store.awaiting_user_reply[
                    "guild:1:text:2:user:3"
                ]
            )
            self.assertEqual(sends, ["delivered help"])

    def test_timeout_is_ambiguous_and_duplicate_invocation_does_not_send(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logs: list[str] = []
            owner, checkpoint, store = self._real_owner(
                Path(temp_dir),
                logs,
            )
            deps = self._real_runtime_deps(
                owner,
                checkpoint,
                store,
                logs,
            )
            attempts: list[str] = []

            class Context:
                guild = SimpleNamespace(id=1)
                channel = SimpleNamespace(id=2)
                author = SimpleNamespace(id=3)
                message = SimpleNamespace(id=93, content="!상태")

                async def send(self, content=None):
                    attempts.append(content)
                    raise TimeoutError("private timeout")

            first = self._real_wrapped(Context(), deps, logs)
            self.assertIsNone(asyncio.run(first.send("uncertain")))
            self.assertEqual(attempts, ["uncertain"])
            self.assertEqual(
                owner.public_status()["phases"]["delivery_ambiguous"],
                1,
            )
            replay = self._real_wrapped(Context(), deps, logs)
            self.assertIsNone(asyncio.run(replay.send("uncertain")))
            self.assertEqual(attempts, ["uncertain"])
            self.assertNotIn("private timeout", " ".join(logs))

    def test_cancellation_is_ambiguous_and_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logs: list[str] = []
            owner, checkpoint, store = self._real_owner(
                Path(temp_dir),
                logs,
            )
            deps = self._real_runtime_deps(
                owner,
                checkpoint,
                store,
                logs,
            )

            class Context:
                guild = SimpleNamespace(id=1)
                channel = SimpleNamespace(id=2)
                author = SimpleNamespace(id=3)
                message = SimpleNamespace(id=94, content="!상태")

                async def send(self, content=None):
                    raise asyncio.CancelledError()

            wrapped = self._real_wrapped(Context(), deps, logs)
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(wrapped.send("uncertain"))
            self.assertEqual(
                owner.public_status()["phases"]["delivery_ambiguous"],
                1,
            )
            history = store.get_conversation_history(
                system_prompt="system",
                session_key="guild:1:text:2:user:3",
            )
            self.assertEqual([row["role"] for row in history], ["system"])

    def test_outer_cancellation_completes_successful_lifecycle_before_propagating(
        self,
    ) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as temp_dir:
                logs: list[str] = []
                owner, checkpoint, store = self._real_owner(
                    Path(temp_dir),
                    logs,
                )
                deps = self._real_runtime_deps(
                    owner,
                    checkpoint,
                    store,
                    logs,
                )
                physical_started = asyncio.Event()
                release_physical = asyncio.Event()
                physical_done = asyncio.Event()

                class Context:
                    guild = SimpleNamespace(id=1)
                    channel = SimpleNamespace(id=2)
                    author = SimpleNamespace(id=3)
                    message = SimpleNamespace(id=941, content="!상태")

                    async def send(self, content=None):
                        physical_started.set()
                        try:
                            await release_physical.wait()
                            return "discord-message"
                        finally:
                            physical_done.set()

                wrapped = self._real_wrapped(Context(), deps, logs)
                send_owner = asyncio.create_task(wrapped.send("uncertain"))
                await asyncio.wait_for(physical_started.wait(), timeout=1.0)

                send_owner.cancel()
                await asyncio.sleep(0)
                send_owner.cancel()
                await asyncio.sleep(0)
                self.assertFalse(send_owner.done())
                self.assertFalse(physical_done.is_set())

                release_physical.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(send_owner, timeout=1.0)
                self.assertTrue(physical_done.is_set())
                self.assertEqual(
                    owner.public_status()["phases"]["completed"],
                    1,
                )
                self.assertEqual(
                    owner.public_status()["phases"]["delivery_ambiguous"],
                    0,
                )
                history = store.get_conversation_history(
                    system_prompt="system",
                    session_key="guild:1:text:2:user:3",
                )
                self.assertEqual(
                    [(row["role"], row["content"]) for row in history],
                    [
                        ("system", "system"),
                        ("user", "!상태"),
                        ("assistant", "uncertain"),
                    ],
                )
                self.assertFalse(
                    store.awaiting_user_reply[
                        "guild:1:text:2:user:3"
                    ]
                )

        asyncio.run(scenario())

    def test_definitive_client_rejection_discards_for_safe_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            logs: list[str] = []
            owner, checkpoint, store = self._real_owner(
                Path(temp_dir),
                logs,
            )
            deps = self._real_runtime_deps(
                owner,
                checkpoint,
                store,
                logs,
            )

            class Rejected(RuntimeError):
                status = 400

            class Context:
                guild = SimpleNamespace(id=1)
                channel = SimpleNamespace(id=2)
                author = SimpleNamespace(id=3)
                message = SimpleNamespace(id=95, content="!상태")

                async def send(self, content=None):
                    raise Rejected("private rejection")

            with self.assertRaises(Rejected):
                asyncio.run(
                    self._real_wrapped(Context(), deps, logs).send(
                        "retryable"
                    )
                )
            self.assertEqual(owner.public_status()["entryCount"], 0)
            retries: list[str] = []

            class RetryContext(Context):
                async def send(self, content=None):
                    retries.append(content)
                    return "retried"

            self.assertEqual(
                asyncio.run(
                    self._real_wrapped(RetryContext(), deps, logs).send(
                        "retryable"
                    )
                ),
                "retried",
            )
            self.assertEqual(retries, ["retryable"])
            self.assertEqual(
                owner.public_status()["phases"]["completed"],
                1,
            )

    def test_recording_context_commits_only_after_successful_text_delivery(
        self,
    ) -> None:
        order: list[object] = []
        delivered = object()

        class Context:
            guild = SimpleNamespace(id=1)
            message = SimpleNamespace(content="!상태")

            async def send(self, content=None, *args, **kwargs):
                order.append(("send", content, args, kwargs))
                return delivered

        original = Context()
        wrapped = ContinuityRecordingCommandContext(
            original,
            record_reply=(
                lambda ctx, user, answer: order.append(
                    ("record", ctx, user, answer)
                )
            ),
            log=lambda *_args, **_kwargs: None,
        )

        result = asyncio.run(wrapped.send("정상", silent=True))

        self.assertIs(result, delivered)
        self.assertEqual(order[0], ("send", "정상", (), {"silent": True}))
        self.assertEqual(
            order[1],
            ("record", original, "!상태", "정상"),
        )

    def test_recording_context_does_not_commit_failed_or_non_text_delivery(
        self,
    ) -> None:
        records: list[object] = []

        class Context:
            message = SimpleNamespace(content="!상태")

            async def send(self, content=None, *args, **kwargs):
                if content == "실패":
                    raise RuntimeError("delivery_failed")
                return "sent"

        wrapped = ContinuityRecordingCommandContext(
            Context(),
            record_reply=lambda *args: records.append(args),
            log=lambda *_args, **_kwargs: None,
        )

        self.assertEqual(asyncio.run(wrapped.send(None)), "sent")
        with self.assertRaisesRegex(RuntimeError, "delivery_failed"):
            asyncio.run(wrapped.send("실패"))
        self.assertEqual(records, [])

    def test_recording_context_contains_record_failure_after_delivery(
        self,
    ) -> None:
        logs: list[tuple[object, ...]] = []

        class Context:
            message = SimpleNamespace(content="")

            async def send(self, content=None):
                return "sent"

        wrapped = ContinuityRecordingCommandContext(
            Context(),
            record_reply=lambda *_args: (_ for _ in ()).throw(
                RuntimeError("private")
            ),
            log=lambda *args, **_kwargs: logs.append(args),
        )

        self.assertEqual(asyncio.run(wrapped.send("정상")), "sent")
        self.assertIn(
            "command_continuity_record_failed",
            str(logs),
        )
        self.assertIn("RuntimeError", str(logs))
        self.assertNotIn("private", str(logs))

    def test_fallback_outer_cancellation_records_success_once(self) -> None:
        async def scenario() -> None:
            physical_started = asyncio.Event()
            release_physical = asyncio.Event()
            order: list[object] = []

            class Context:
                message = SimpleNamespace(content="!상태")

                async def send(self, content=None):
                    physical_started.set()
                    await release_physical.wait()
                    order.append(("send", content))
                    return "discord-message"

            wrapped = ContinuityRecordingCommandContext(
                Context(),
                record_reply=lambda *_args: order.append("record"),
                log=lambda *_args, **_kwargs: None,
            )
            send_owner = asyncio.create_task(wrapped.send("answer"))
            await asyncio.wait_for(physical_started.wait(), timeout=1.0)

            send_owner.cancel()
            await asyncio.sleep(0)
            send_owner.cancel()
            await asyncio.sleep(0)
            self.assertFalse(send_owner.done())

            release_physical.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(send_owner, timeout=1.0)
            self.assertEqual(order, [("send", "answer"), "record"])

        asyncio.run(scenario())

    def test_post_delivery_hook_failure_still_records_delivered_text(
        self,
    ) -> None:
        order: list[object] = []

        class Context:
            message = SimpleNamespace(content="!restart")

            async def send(self, content=None):
                order.append(("send", content))
                return "sent"

        wrapped = ContinuityRecordingCommandContext(
            Context(),
            record_reply=lambda *_args: order.append("record"),
            log=lambda *_args, **_kwargs: None,
        )

        def fail_after_delivery() -> None:
            order.append("hook")
            raise RuntimeError("terminal scheduling failed")

        with self.assertRaisesRegex(
            RuntimeError,
            "terminal scheduling failed",
        ):
            asyncio.run(
                wrapped.send_with_post_delivery_hook(
                    "재시작",
                    after_delivery=fail_after_delivery,
                )
            )

        self.assertEqual(
            order,
            [("send", "재시작"), "hook", "record"],
        )

    def test_mark_text_session_from_command_records_turn_with_message_context(self) -> None:
        calls: list[tuple] = []
        commits: list[tuple[object, ...]] = []
        thread_checks: list[object] = []

        def resolve_text_thread_id(channel, *, is_thread_parent):
            thread_checks.append(is_thread_parent(channel.parent))
            return 77

        def make_text_session_key(guild_id, channel_id, user_id, *, thread_id=None):
            return f"{guild_id}:{channel_id}:{user_id}:{thread_id}"

        deps = DiscordCommandSessionRuntimeDeps(
            resolve_text_thread_id=resolve_text_thread_id,
            is_text_thread_parent=lambda parent: getattr(parent, "is_text_channel", False),
            make_text_session_key=make_text_session_key,
            start_new_turn=lambda session_key: (
                f"command-turn:{session_key}"
            ),
            record_command_assistant_turn=lambda *args, **kwargs: calls.append((args, kwargs)),
            system_prompt="system",
            max_history_items=12,
            normal_ttl_sec=30.0,
            question_ttl_sec=45.0,
            commit_session_continuity=lambda *args: (
                commits.append(args)
                or durable_continuity_status(3)
            ),
            log=lambda *args, **kwargs: None,
        )
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=2, parent=SimpleNamespace(is_text_channel=True)),
            author=SimpleNamespace(id=3),
            message=SimpleNamespace(id=4),
        )

        mark_text_session_from_command_runtime(
            ctx,
            "user",
            "answer",
            awaiting_user_reply=True,
            deps=deps,
        )

        self.assertEqual(thread_checks, [True])
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            commits,
            [("1:2:3:77", "command-turn:1:2:3:77")],
        )
        args, kwargs = calls[0]
        self.assertEqual(args, ("1:2:3:77", "user", "answer"))
        self.assertEqual(
            kwargs,
            {
                "system_prompt": "system",
                "max_history_items": 12,
                "guild_id": 1,
                "user_id": 3,
                "channel_id": 2,
                "message_id": 4,
                "awaiting_user_reply": True,
                "normal_ttl_sec": 30.0,
                "question_ttl_sec": 45.0,
            },
        )

    def test_mark_text_session_from_command_ignores_dm_context(self) -> None:
        calls: list[object] = []
        deps = DiscordCommandSessionRuntimeDeps(
            resolve_text_thread_id=lambda *args, **kwargs: 1,
            is_text_thread_parent=lambda parent: True,
            make_text_session_key=lambda *args, **kwargs: "session",
            start_new_turn=lambda session_key: f"turn:{session_key}",
            record_command_assistant_turn=lambda *args, **kwargs: calls.append((args, kwargs)),
            system_prompt="system",
            max_history_items=12,
            normal_ttl_sec=30.0,
            question_ttl_sec=45.0,
            commit_session_continuity=(
                lambda *_args: durable_continuity_status(1)
            ),
            log=lambda *args, **kwargs: None,
        )

        mark_text_session_from_command_runtime(
            SimpleNamespace(guild=None),
            "user",
            "answer",
            deps=deps,
        )

        self.assertEqual(calls, [])

    def test_partial_commit_status_raises_fixed_failure(
        self,
    ) -> None:
        logs: list[tuple] = []
        private = (
            "Bearer command-continuity-secret "
            r"C:\Users\Admin\checkpoint.json"
        )
        deps = DiscordCommandSessionRuntimeDeps(
            resolve_text_thread_id=lambda *_args, **_kwargs: None,
            is_text_thread_parent=lambda _parent: False,
            make_text_session_key=(
                lambda *_args, **_kwargs: "session"
            ),
            start_new_turn=(
                lambda session_key: f"turn:{session_key}"
            ),
            record_command_assistant_turn=(
                lambda *_args, **_kwargs: None
            ),
            system_prompt="system",
            max_history_items=12,
            normal_ttl_sec=30.0,
            question_ttl_sec=45.0,
            commit_session_continuity=lambda *_args: {
                "state": "ready",
                "privateMessage": private,
            },
            log=lambda *args, **_kwargs: logs.append(args),
        )
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=2),
            author=SimpleNamespace(id=3),
            message=SimpleNamespace(id=4),
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "conversation_continuity_commit_failed",
        ):
            mark_text_session_from_command_runtime(
                ctx,
                "user",
                "answer",
                deps=deps,
            )

        rendered = str(logs)
        self.assertEqual(logs, [])
        self.assertNotIn("command-continuity-secret", rendered)
        self.assertNotIn("Users", rendered)


if __name__ == "__main__":
    unittest.main()
