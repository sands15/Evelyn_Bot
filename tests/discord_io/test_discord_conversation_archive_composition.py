from __future__ import annotations

import asyncio
import hashlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_conversation_archive_composition import (  # noqa: E402
    DiscordConversationArchiveComposition,
    active_task_planner_guidance_from_composition,
)


class _StopWorker(RuntimeError):
    pass


def _work_order(*, remaining: list[str] | None = None) -> dict[str, object]:
    return {
        "requestId": "1" * 32,
        "deletionGeneration": 7,
        "scopeDigest": "a" * 64,
        "reason": "user_requested",
        "requestedAt": "2026-08-28T00:00:00+00:00",
        "scopeAll": False,
        "guildId": "7",
        "startedAt": "2026-08-27T00:00:00+00:00",
        "endedAt": "2026-08-28T00:00:00+00:00",
        "lineageHandles": [{"kind": "turn", "digest": "b" * 64}],
        "lineageComplete": True,
        "remainingSinks": list(remaining or ["ingress_journal"]),
        "contentFree": True,
    }


class DiscordConversationArchiveCompositionTests(
    unittest.IsolatedAsyncioTestCase
):
    @staticmethod
    def _client(
        *,
        work_orders: tuple[dict[str, object], ...] = (),
        otp_deliveries: tuple[dict[str, object], ...] = (),
    ) -> SimpleNamespace:
        return SimpleNamespace(
            poll_otp_deliveries=AsyncMock(return_value=otp_deliveries),
            acknowledge_otp_delivery=AsyncMock(),
            poll_purge_owner_work=AsyncMock(return_value=work_orders),
            acknowledge_purge_owner_receipt=AsyncMock(),
        )

    @staticmethod
    def _composition(
        *,
        client: SimpleNamespace,
        callback: object,
        completed: object | None = None,
        bot: object | None = None,
        record_error: Mock | None = None,
        sleep: AsyncMock | None = None,
    ) -> DiscordConversationArchiveComposition:
        return DiscordConversationArchiveComposition(
            client=client,
            shared_sessions=None,
            gate=None,
            _bot=bot or SimpleNamespace(),
            _record_error=record_error or Mock(),
            _sleep=sleep or AsyncMock(side_effect=_StopWorker),
            _purge_owner_callback=callback,  # type: ignore[arg-type]
            _purge_owner_completed=completed,  # type: ignore[arg-type]
        )

    async def test_one_loop_preserves_otp_and_acks_only_callback_subset(
        self,
    ) -> None:
        user = SimpleNamespace(send=AsyncMock())
        bot = SimpleNamespace(
            get_user=Mock(return_value=user),
            fetch_user=AsyncMock(),
        )
        work = _work_order(
            remaining=["ingress_journal", "tts_buffer"]
        )
        client = self._client(
            work_orders=(work,),
            otp_deliveries=(
                {
                    "deliveryId": "delivery-1",
                    "discordUserId": "123456789",
                    "code": "A1b2",
                    "expiresAt": 100,
                },
            ),
        )
        callback = AsyncMock(return_value=["tts_buffer"])
        sleep = AsyncMock(side_effect=_StopWorker)
        composition = self._composition(
            client=client,
            callback=callback,
            bot=bot,
            sleep=sleep,
        )

        with self.assertRaises(_StopWorker):
            await composition.deliver_admin_otps()

        user.send.assert_awaited_once()
        client.acknowledge_otp_delivery.assert_awaited_once_with(
            delivery_id="delivery-1",
            delivered=True,
        )
        callback.assert_awaited_once_with(work)
        client.acknowledge_purge_owner_receipt.assert_awaited_once_with(
            request_id="1" * 32,
            deletion_generation=7,
            scope_digest="a" * 64,
            sink="tts_buffer",
        )
        sleep.assert_awaited_once_with(1.0)

    async def test_only_final_archive_completed_ack_releases_exact_fence(
        self,
    ) -> None:
        work = _work_order(
            remaining=["ingress_journal", "tts_buffer"]
        )
        client = self._client(work_orders=(work,))
        client.acknowledge_purge_owner_receipt.side_effect = (
            {"archiveCompleted": False},
            {"archiveCompleted": True},
        )
        callback = AsyncMock(
            return_value=["ingress_journal", "tts_buffer"]
        )
        completed = AsyncMock()
        composition = self._composition(
            client=client,
            callback=callback,
            completed=completed,
        )

        with self.assertRaises(_StopWorker):
            await composition.deliver_admin_otps()

        self.assertEqual(
            [call.kwargs["sink"] for call in client.acknowledge_purge_owner_receipt.await_args_list],
            ["ingress_journal", "tts_buffer"],
        )
        completed.assert_awaited_once_with(work)

    async def test_incomplete_or_lost_last_ack_never_releases_fence(
        self,
    ) -> None:
        for ack_result in (
            {"archiveCompleted": False},
            OSError("response lost"),
        ):
            with self.subTest(result=type(ack_result).__name__):
                client = self._client(work_orders=(_work_order(),))
                if isinstance(ack_result, Exception):
                    client.acknowledge_purge_owner_receipt.side_effect = (
                        ack_result
                    )
                else:
                    client.acknowledge_purge_owner_receipt.return_value = (
                        ack_result
                    )
                completed = Mock()
                composition = self._composition(
                    client=client,
                    callback=AsyncMock(return_value=["ingress_journal"]),
                    completed=completed,
                )

                with self.assertRaises(_StopWorker):
                    await composition.deliver_admin_otps()

                completed.assert_not_called()

    async def test_sync_completion_callback_is_supported(self) -> None:
        work = _work_order()
        client = self._client(work_orders=(work,))
        client.acknowledge_purge_owner_receipt.return_value = {
            "archiveCompleted": True
        }
        completed = Mock()
        composition = self._composition(
            client=client,
            callback=AsyncMock(return_value=["ingress_journal"]),
            completed=completed,
        )

        with self.assertRaises(_StopWorker):
            await composition.deliver_admin_otps()

        completed.assert_called_once_with(work)

    async def test_invalid_or_raised_callback_acks_nothing(self) -> None:
        cases = (
            "ingress_journal",
            ("ingress_journal", "ingress_journal"),
            ("tts_buffer",),
            (1,),
            RuntimeError("private cleanup detail"),
        )
        for result in cases:
            with self.subTest(result=type(result).__name__):
                client = self._client(work_orders=(_work_order(),))
                callback = (
                    AsyncMock(side_effect=result)
                    if isinstance(result, Exception)
                    else AsyncMock(return_value=result)
                )
                errors = Mock()
                composition = self._composition(
                    client=client,
                    callback=callback,
                    record_error=errors,
                )

                with self.assertRaises(_StopWorker):
                    await composition.deliver_admin_otps()

                client.acknowledge_purge_owner_receipt.assert_not_awaited()
                errors.assert_called_once()
                self.assertEqual(
                    errors.call_args.args[0],
                    "conversation_archive_purge_owner_failed",
                )

    async def test_callback_cannot_mutate_remaining_scope_before_validation(
        self,
    ) -> None:
        work = _work_order()
        client = self._client(work_orders=(work,))

        async def mutate_scope(candidate: dict[str, object]) -> list[str]:
            remaining = candidate["remainingSinks"]
            assert isinstance(remaining, list)
            remaining.append("tts_buffer")
            return ["tts_buffer"]

        errors = Mock()
        composition = self._composition(
            client=client,
            callback=mutate_scope,
            record_error=errors,
        )

        with self.assertRaises(_StopWorker):
            await composition.deliver_admin_otps()

        self.assertEqual(work["remainingSinks"], ["ingress_journal"])
        client.acknowledge_purge_owner_receipt.assert_not_awaited()
        errors.assert_called_once()

    async def test_no_callback_keeps_existing_otp_only_loop(self) -> None:
        client = self._client()
        composition = self._composition(client=client, callback=None)

        with self.assertRaises(_StopWorker):
            await composition.deliver_admin_otps()

        client.poll_otp_deliveries.assert_awaited_once()
        client.poll_purge_owner_work.assert_not_awaited()

    async def test_purge_one_shot_never_touches_otp_surface(self) -> None:
        work = _work_order()
        client = self._client(work_orders=(work,))
        callback = AsyncMock(return_value=["ingress_journal"])
        composition = self._composition(
            client=client,
            callback=callback,
        )

        await composition.process_purge_owner_work_once()

        client.poll_otp_deliveries.assert_not_awaited()
        client.poll_purge_owner_work.assert_awaited_once_with()
        callback.assert_awaited_once_with(work)
        client.acknowledge_purge_owner_receipt.assert_awaited_once()

    async def test_purge_only_and_otp_loops_never_poll_concurrently(
        self,
    ) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def purge(_work: dict[str, object]) -> list[str]:
            entered.set()
            await release.wait()
            return ["ingress_journal"]

        client = self._client(work_orders=(_work_order(),))
        composition = self._composition(
            client=client,
            callback=purge,
        )
        worker = asyncio.create_task(composition.run_purge_owner_loop())
        await entered.wait()

        await composition.deliver_admin_otps()

        client.poll_otp_deliveries.assert_not_awaited()
        client.poll_purge_owner_work.assert_awaited_once_with()
        release.set()
        with self.assertRaises(_StopWorker):
            await worker

    def test_build_rejects_non_callable_purge_owner(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "conversation_archive_purge_owner_invalid",
        ):
            DiscordConversationArchiveComposition.build(
                enabled=False,
                base_url="",
                ingest_key_file="",
                user_view_key_file="",
                shared_session_ttl_seconds=1.0,
                get_http_session=AsyncMock(),
                bot=object(),
                record_error=Mock(),
                purge_owner_callback="invalid",  # type: ignore[arg-type]
            )

    async def test_task_guidance_adapter_uses_late_bound_archive_client(self) -> None:
        guidance_text = "verified evidence를 먼저 확인한다."
        binding = {
            "versionId": "guidance-v3",
            "guidance": guidance_text,
            "guidanceDigest": hashlib.sha256(
                guidance_text.encode("utf-8")
            ).hexdigest(),
        }
        client = SimpleNamespace(
            active_task_guidance=AsyncMock(return_value=binding)
        )
        composition = SimpleNamespace(client=client)

        guidance = await active_task_planner_guidance_from_composition(
            lambda: composition
        )
        disabled = await active_task_planner_guidance_from_composition(
            lambda: None
        )

        assert guidance is not None
        self.assertEqual(guidance.version_id, "guidance-v3")
        self.assertEqual(guidance.guidance, binding["guidance"])
        self.assertEqual(
            guidance.guidance_digest, binding["guidanceDigest"]
        )
        self.assertIsNone(disabled)
        client.active_task_guidance.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()
