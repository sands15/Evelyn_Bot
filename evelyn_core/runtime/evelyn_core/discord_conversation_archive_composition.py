from __future__ import annotations

import asyncio
import copy
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .discord_conversation_archive_runtime import (
    DiscordConversationArchiveClient,
    DiscordSharedArchiveGate,
    DiscordSharedSessionRegistry,
)
from .task_loop_runtime import TaskPlannerGuidance


@dataclass(frozen=True, slots=True)
class DiscordConversationArchiveComposition:
    """Own the archive graph and its single OTP/purge polling worker.

    The optional purge callback returns after local cleanup and negative recall
    while keeping the writer fence frozen.  The completion callback releases
    that exact fence only after the final acknowledgement proves archive
    completion.
    """

    client: DiscordConversationArchiveClient | None
    shared_sessions: DiscordSharedSessionRegistry | None
    gate: DiscordSharedArchiveGate | None
    _bot: Any
    _record_error: Callable[[str, Exception], Any]
    _sleep: Callable[[float], Awaitable[Any]]
    _purge_owner_callback: Callable[
        [dict[str, Any]], Awaitable[tuple[str, ...] | list[str]]
    ] | None = None
    _purge_owner_completed: Callable[[dict[str, Any]], Any] | None = None
    _poll_worker_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        compare=False,
        repr=False,
    )

    @classmethod
    def build(
        cls,
        *,
        enabled: bool,
        base_url: str,
        ingest_key_file: str,
        user_view_key_file: str,
        shared_session_ttl_seconds: float,
        get_http_session: Callable[[], Awaitable[Any]],
        bot: Any,
        record_error: Callable[[str, Exception], Any],
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        purge_owner_callback: Callable[
            [dict[str, Any]], Awaitable[tuple[str, ...] | list[str]]
        ] | None = None,
        purge_owner_completed: Callable[[dict[str, Any]], Any] | None = None,
    ) -> "DiscordConversationArchiveComposition":
        if any(
            callback is not None and not callable(callback)
            for callback in (purge_owner_callback, purge_owner_completed)
        ):
            raise ValueError("conversation_archive_purge_owner_invalid")
        if not enabled:
            return cls(
                None,
                None,
                None,
                bot,
                record_error,
                sleep,
                purge_owner_callback,
                purge_owner_completed,
            )
        if not (ingest_key_file and user_view_key_file):
            raise RuntimeError("conversation_archive_key_paths_required")
        client = DiscordConversationArchiveClient.from_key_file(
            base_url=base_url,
            key_file=ingest_key_file,
            user_view_key_file=user_view_key_file,
            get_http_session=get_http_session,
        )
        shared_sessions = DiscordSharedSessionRegistry(
            ttl_seconds=shared_session_ttl_seconds,
        )
        return cls(
            client,
            shared_sessions,
            DiscordSharedArchiveGate(client=client, sessions=shared_sessions),
            bot,
            record_error,
            sleep,
            purge_owner_callback,
            purge_owner_completed,
        )

    async def deliver_admin_otps(self) -> None:
        """Run the Discord OTP loop and, when owned, remote purge polling."""

        await self._run_poll_worker(include_otp=True)

    async def run_purge_owner_loop(self) -> None:
        """Run purge polling without touching the Discord OTP surface."""

        if self._purge_owner_callback is None:
            return
        await self._run_poll_worker(include_otp=False)

    async def process_purge_owner_work_once(self) -> None:
        """Process one bounded, OTP-free remote purge poll."""

        client = self.client
        if (
            client is None
            or self._purge_owner_callback is None
            or self._poll_worker_lock.locked()
        ):
            return
        async with self._poll_worker_lock:
            await self._process_purge_owner_work(client)

    async def _run_poll_worker(self, *, include_otp: bool) -> None:
        client = self.client
        if client is None or self._poll_worker_lock.locked():
            return
        await self._poll_worker_lock.acquire()
        try:
            while True:
                if include_otp:
                    await self._deliver_admin_otps_once(client)
                if self._purge_owner_callback is not None:
                    try:
                        await self._process_purge_owner_work(client)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self._record_error(
                            "conversation_archive_purge_owner_failed",
                            exc,
                        )
                await self._sleep(1.0)
        finally:
            self._poll_worker_lock.release()

    async def _deliver_admin_otps_once(
        self,
        client: DiscordConversationArchiveClient,
    ) -> None:
        try:
            deliveries = await client.poll_otp_deliveries()
            for delivery in deliveries:
                delivery_id = str(delivery.get("deliveryId") or "")
                user_id_text = str(delivery.get("discordUserId") or "")
                code = str(delivery.get("code") or "")
                delivered = False
                if (
                    delivery_id
                    and user_id_text.isdecimal()
                    and len(code) == 4
                    and code.isascii()
                    and code.isalnum()
                ):
                    try:
                        user_id = int(user_id_text)
                        user = self._bot.get_user(user_id)
                        if user is None:
                            user = await self._bot.fetch_user(user_id)
                        await user.send(
                            "이블린 로컬 관리자 확인 코드: "
                            f"`{code}`\n60초 안에 로컬 Control Page에 입력해줘."
                        )
                        delivered = True
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        delivered = False
                if delivery_id:
                    await client.acknowledge_otp_delivery(
                        delivery_id=delivery_id,
                        delivered=delivered,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_error(
                "conversation_archive_otp_delivery_failed",
                exc,
            )

    async def _process_purge_owner_work(
        self,
        client: DiscordConversationArchiveClient,
    ) -> None:
        callback = self._purge_owner_callback
        if callback is None:
            return
        for work_order in await client.poll_purge_owner_work():
            try:
                remaining = work_order.get("remainingSinks")
                completed = await callback(copy.deepcopy(work_order))
                if (
                    not isinstance(completed, (tuple, list))
                    or not isinstance(remaining, list)
                    or any(not isinstance(sink, str) for sink in completed)
                    or len(completed) != len(set(completed))
                    or not set(completed).issubset(set(remaining))
                ):
                    raise ValueError(
                        "conversation_archive_purge_owner_result_invalid"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._record_error(
                    "conversation_archive_purge_owner_failed",
                    exc,
                )
                continue
            last_ack: dict[str, Any] | None = None
            for sink in completed:
                last_ack = await client.acknowledge_purge_owner_receipt(
                    request_id=work_order["requestId"],
                    deletion_generation=work_order[
                        "deletionGeneration"
                    ],
                    scope_digest=work_order["scopeDigest"],
                    sink=sink,
                )
            if (
                completed
                and isinstance(last_ack, dict)
                and last_ack.get("archiveCompleted") is True
                and self._purge_owner_completed is not None
            ):
                released = self._purge_owner_completed(
                    copy.deepcopy(work_order)
                )
                if inspect.isawaitable(released):
                    await released


async def active_task_planner_guidance_from_composition(
    get_composition: Callable[[], DiscordConversationArchiveComposition | None],
    **_kwargs: Any,
) -> TaskPlannerGuidance | None:
    """Resolve active advisory guidance without making ``main`` an owner."""

    composition = get_composition()
    client = getattr(composition, "client", None)
    if client is None:
        return None
    binding = await client.active_task_guidance()
    return TaskPlannerGuidance(
        version_id=binding["versionId"],
        guidance=binding["guidance"],
        guidance_digest=binding["guidanceDigest"],
    )


__all__ = [
    "DiscordConversationArchiveComposition",
    "active_task_planner_guidance_from_composition",
]
