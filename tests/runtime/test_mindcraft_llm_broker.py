from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import contextmanager, suppress
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import memory_deletion_journal as journal  # noqa: E402
from evelyn_core import memory_exposure  # noqa: E402
from evelyn_core import mindcraft_llm_broker as broker  # noqa: E402
from evelyn_core.memory_integrity_authenticity import (  # noqa: E402
    MEMORY_INTEGRITY_ANCHOR_DIR_ENV,
    MEMORY_INTEGRITY_BOOTSTRAP_ENV,
    MEMORY_INTEGRITY_KEY_FILE_ENV,
)


NOT_USED_REF = {
    "schema": "conversation.memory-receipt-ref.v1",
    "state": "not_used",
    "memoryVersion": 0,
    "suppliedNoteIds": [],
    "suppliedNoteCount": 0,
    "contentFree": True,
}


def bound_ref(*, version: int = 1) -> dict[str, object]:
    return {
        "schema": "conversation.memory-receipt-ref.v1",
        "state": "bound",
        "memoryVersion": version,
        "suppliedNoteIds": ["concept-0123456789abcdef"],
        "suppliedNoteCount": 1,
        "contentFree": True,
    }


def request_payload(
    *,
    kind: str = "chat",
    messages: list[dict[str, object]] | None = None,
    history_ref: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema": broker.MINDCRAFT_LLM_REQUEST_SCHEMA,
        "requestId": str(uuid.uuid4()),
        "requestKind": kind,
        "messages": messages
        or [
            {
                "role": "user",
                "content": "hello",
                "memoryReceiptRef": dict(NOT_USED_REF),
            }
        ],
        "historyReceiptRef": dict(history_ref or NOT_USED_REF),
    }


def tombstone_payload(note_id: str) -> dict[str, object]:
    return {
        "schema": journal.MEMORY_DELETE_TOMBSTONE_V1_SCHEMA,
        "noteId": note_id,
        "noteType": "concept",
        "sourceType": "conversation",
        "deletedAt": "2026-08-09T00:00:00Z",
        "reason": "privacy_request",
    }


def write_memory_version(index_dir: Path, version: int) -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        index_dir / memory_exposure.MEMORY_INDEX_DB_NAME
    )
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS metadata "
            "(key TEXT PRIMARY KEY, value NOT NULL)"
        )
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES('memory_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(version),),
        )
        connection.commit()
    finally:
        connection.close()


def seed_journal(index_dir: Path) -> None:
    journal.append_memory_deletion_tombstone(
        index_dir,
        tombstone_payload("concept-fedcba9876543210"),
    )


class MindcraftLlmBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.token_path = self.root / "broker-token" / "token"
        self.token_path.parent.mkdir()
        self.epoch_path = self.root / "qwen-epoch"
        self.epoch_path.write_text(
            "11111111-1111-4111-8111-111111111111\n",
            encoding="utf-8",
        )
        self.environment = patch.dict(
            os.environ,
            {
                broker.MINDCRAFT_LLM_TOKEN_FILE_ENV: str(
                    self.token_path
                ),
                broker.MINDCRAFT_QWEN_EPOCH_FILE_ENV: str(self.epoch_path),
                MEMORY_INTEGRITY_KEY_FILE_ENV: "",
                MEMORY_INTEGRITY_ANCHOR_DIR_ENV: "",
                MEMORY_INTEGRITY_BOOTSTRAP_ENV: "",
            },
        )
        self.environment.start()
        self.memory_root = patch.object(broker, "MEMORY_ROOT", self.root)
        self.memory_root.start()
        self.clients: list[TestClient] = []
        self.servers: list[TestServer] = []

    async def asyncTearDown(self) -> None:
        for client in reversed(self.clients):
            with suppress(Exception):
                await client.close()
        for server in reversed(self.servers):
            with suppress(Exception):
                await server.close()
        self.memory_root.stop()
        self.environment.stop()
        self.temporary.cleanup()

    async def start_server(self, app: web.Application) -> TestServer:
        server = TestServer(app)
        await server.start_server()
        self.servers.append(server)
        return server

    async def start_broker(self) -> tuple[TestClient, str]:
        app = web.Application()
        broker.install_mindcraft_llm_broker(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        self.clients.append(client)
        return client, self.token_path.read_text(encoding="utf-8").strip()

    @staticmethod
    def headers(token: str) -> dict[str, str]:
        return {broker.MINDCRAFT_LLM_TOKEN_HEADER: token}

    async def complete(
        self,
        client: TestClient,
        response: object,
        frame: dict[str, object],
        token: str,
        *,
        outcome: str = "delivered",
    ) -> None:
        lease = frame["deliveryLease"]
        self.assertIsInstance(lease, dict)
        ack_task = asyncio.create_task(
            client.post(
                "/internal/mindcraft-llm/ack",
                headers=self.headers(token),
                json={
                    "schema": broker.MINDCRAFT_LLM_DELIVERY_ACK_SCHEMA,
                    "requestId": frame["requestId"],
                    "leaseId": lease["leaseId"],
                    "outcome": outcome,
                    "contentFree": True,
                },
            )
        )
        await asyncio.wait_for(response.read(), timeout=2)
        ack = await asyncio.wait_for(ack_task, timeout=2)
        self.assertEqual(ack.status, 200, await ack.text())
        self.assertTrue((await ack.json())["contentFree"])

    async def test_auth_shape_and_fixed_routes_fail_closed(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        redirected_calls = 0

        async def upstream(request: web.Request) -> web.Response:
            calls.append((request.path, await request.json()))
            return web.json_response(
                {"choices": [{"message": {"content": "safe result"}}]}
            )

        async def redirect(_request: web.Request) -> web.Response:
            raise web.HTTPFound(location="/redirect-target")

        async def redirect_target(_request: web.Request) -> web.Response:
            nonlocal redirected_calls
            redirected_calls += 1
            return web.json_response(
                {"choices": [{"message": {"content": "unsafe redirect"}}]}
            )

        upstream_app = web.Application()
        upstream_app.router.add_post("/local", upstream)
        upstream_app.router.add_post("/router", upstream)
        upstream_app.router.add_post("/redirect", redirect)
        upstream_app.router.add_post("/redirect-target", redirect_target)
        upstream_server = await self.start_server(upstream_app)

        with patch.dict(
            os.environ,
            {broker.MINDCRAFT_LLM_TOKEN_FILE_ENV: ""},
        ):
            unconfigured_app = web.Application()
            broker.install_mindcraft_llm_broker(unconfigured_app)
            unconfigured = TestClient(TestServer(unconfigured_app))
            await unconfigured.start_server()
            self.clients.append(unconfigured)
            unavailable = await unconfigured.post(
                "/internal/mindcraft-llm",
                json=request_payload(),
            )
            self.assertEqual(unavailable.status, 503)
            self.assertEqual(
                (await unavailable.json())["error"],
                "mindcraft_llm_broker_unconfigured",
            )
        with (
            patch.object(
                broker,
                "MINDCRAFT_LOCAL_LLM_URL",
                str(upstream_server.make_url("/local")),
            ),
            patch.object(
                broker,
                "MINDCRAFT_ROUTER_LLM_URL",
                str(upstream_server.make_url("/router")),
            ),
        ):
            client, token = await self.start_broker()

            missing = await client.post(
                "/internal/mindcraft-llm",
                json=request_payload(),
            )
            self.assertEqual(missing.status, 403)
            wrong = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers("x" * 64),
                json=request_payload(),
            )
            self.assertEqual(wrong.status, 403)
            invalid = request_payload()
            invalid["url"] = "http://PRIVATE_OVERRIDE_CANARY.invalid"
            rejected = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers(token),
                json=invalid,
            )
            self.assertEqual(rejected.status, 400)
            self.assertEqual(calls, [])
            self.assertNotIn(
                "PRIVATE_OVERRIDE_CANARY",
                await rejected.text(),
            )

            local_request = request_payload(kind="action")
            local_response = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers(token),
                json=local_request,
            )
            local_frame = json.loads(
                (await local_response.content.readline()).decode("utf-8")
            )
            await self.complete(client, local_response, local_frame, token)
            replayed = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers(token),
                json=local_request,
            )
            self.assertEqual(replayed.status, 409)
            self.assertEqual(
                (await replayed.json())["error"],
                "mindcraft_llm_request_replayed",
            )

            router_response = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers(token),
                json=request_payload(kind="router"),
            )
            router_frame = json.loads(
                (await router_response.content.readline()).decode("utf-8")
            )
            await self.complete(client, router_response, router_frame, token)

            with patch.object(
                broker,
                "MINDCRAFT_LOCAL_LLM_URL",
                str(upstream_server.make_url("/redirect")),
            ):
                redirected = await client.post(
                    "/internal/mindcraft-llm",
                    headers=self.headers(token),
                    json=request_payload(),
                )
                self.assertEqual(redirected.status, 503)
                self.assertEqual(redirected_calls, 0)
                self.assertTrue(
                    client.server.app[broker._QWEN_ADMISSION].available
                )
                self.assertFalse(
                    client.server.app[broker._QWEN_INFLIGHT_MARKER].exists()
                )

        self.assertEqual([path for path, _payload in calls], ["/local", "/router"])
        local_payload = calls[0][1]
        router_payload = calls[1][1]
        self.assertEqual(local_payload["model"], broker.MINDCRAFT_LOCAL_MODEL)
        self.assertEqual(local_payload["max_tokens"], 64)
        self.assertEqual(router_payload["model"], broker.MINDCRAFT_ROUTER_MODEL)
        self.assertEqual(router_payload["max_tokens"], 24)
        self.assertNotIn("memoryReceiptRef", json.dumps(calls))

    async def test_replay_window_preserves_active_and_evicts_oldest_completed(self) -> None:
        app = web.Application()
        app[broker._LEASES] = {}
        now = asyncio.get_running_loop().time()
        active_id, oldest_completed_id, latest_id = (
            str(uuid.uuid4()) for _ in range(3)
        )
        app[broker._SEEN_REQUESTS] = {
            active_id: now - 1,
            oldest_completed_id: now + 200,
            latest_id: now + 300,
        }
        loop = asyncio.get_running_loop()
        app[broker._LEASES]["active"] = broker._DeliveryLease(
            request_id=active_id,
            acknowledged=loop.create_future(),
            released=loop.create_future(),
        )
        new_id = str(uuid.uuid4())

        with patch.object(broker, "MINDCRAFT_LLM_MAX_SEEN_REQUESTS", 3):
            broker._admit_request_id(app, new_id)
            self.assertNotIn(
                oldest_completed_id,
                app[broker._SEEN_REQUESTS],
            )
            for replayed_id in (active_id, latest_id, new_id):
                with self.assertRaises(broker._RequestError) as raised:
                    broker._admit_request_id(app, replayed_id)
                self.assertEqual(raised.exception.code, "mindcraft_llm_request_replayed")
                self.assertEqual(raised.exception.status, 409)

    async def test_delivery_ack_holds_real_edit_and_delete_writers(self) -> None:
        index_dir = self.root / "memory_index"
        write_memory_version(index_dir, 1)
        seed_journal(index_dir)

        upstream_calls = 0

        async def upstream(_request: web.Request) -> web.Response:
            nonlocal upstream_calls
            upstream_calls += 1
            return web.json_response(
                {"choices": [{"message": {"content": "bound result"}}]}
            )

        upstream_app = web.Application()
        upstream_app.router.add_post("/local", upstream)
        upstream_server = await self.start_server(upstream_app)
        with patch.object(
            broker,
            "MINDCRAFT_LOCAL_LLM_URL",
            str(upstream_server.make_url("/local")),
        ):
            client, token = await self.start_broker()
            receipt = bound_ref()
            response = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers(token),
                json=request_payload(
                    kind="action",
                    history_ref=receipt,
                    messages=[
                        {
                            "role": "assistant",
                            "content": "previous answer",
                            "memoryReceiptRef": receipt,
                        },
                        {
                            "role": "system",
                            "content": "previous action result",
                            "memoryReceiptRef": receipt,
                        },
                        {
                            "role": "user",
                            "content": "continue",
                            "memoryReceiptRef": NOT_USED_REF,
                        },
                    ],
                ),
            )
            frame = json.loads(
                (await response.content.readline()).decode("utf-8")
            )
            self.assertEqual(response.status, 200, frame)
            self.assertEqual(frame["memoryReceiptRef"]["state"], "bound")

            def edit_version() -> None:
                with journal.memory_deletion_journal_guard(
                    index_dir,
                    require_stable=True,
                ):
                    write_memory_version(index_dir, 2)

            def delete_note() -> None:
                journal.append_memory_deletion_tombstone(
                    index_dir,
                    tombstone_payload("concept-0123456789abcdef"),
                )

            with self.assertRaises(journal.MemoryDeletionJournalBusyError):
                await asyncio.to_thread(edit_version)
            with self.assertRaises(journal.MemoryDeletionJournalBusyError):
                await asyncio.to_thread(delete_note)

            wrong = await client.post(
                "/internal/mindcraft-llm/ack",
                headers=self.headers(token),
                json={
                    "schema": broker.MINDCRAFT_LLM_DELIVERY_ACK_SCHEMA,
                    "requestId": frame["requestId"],
                    "leaseId": "f" * 64,
                    "outcome": "delivered",
                    "contentFree": True,
                },
            )
            self.assertEqual(wrong.status, 409)
            with self.assertRaises(journal.MemoryDeletionJournalBusyError):
                await asyncio.to_thread(edit_version)

            await self.complete(client, response, frame, token)
            await asyncio.to_thread(edit_version)
            await asyncio.to_thread(delete_note)
            self.assertEqual(upstream_calls, 1)

    async def test_filter_race_and_disconnect_release_without_replay(
        self,
    ) -> None:
        index_dir = self.root / "memory_index"
        write_memory_version(index_dir, 1)
        seed_journal(index_dir)
        upstream_calls = 0

        async def upstream(_request: web.Request) -> web.Response:
            nonlocal upstream_calls
            upstream_calls += 1
            return web.json_response(
                {"choices": [{"message": {"content": "result"}}]}
            )

        upstream_app = web.Application()
        upstream_app.router.add_post("/local", upstream)
        upstream_server = await self.start_server(upstream_app)
        original_filter = broker.filter_conversation_history_for_memory_exposure

        def filter_then_edit(*args: object, **kwargs: object) -> object:
            outcome = original_filter(*args, **kwargs)
            with journal.memory_deletion_journal_guard(
                index_dir,
                require_stable=True,
            ):
                write_memory_version(index_dir, 2)
            return outcome

        with (
            patch.object(
                broker,
                "MINDCRAFT_LOCAL_LLM_URL",
                str(upstream_server.make_url("/local")),
            ),
            patch.object(
                broker,
                "filter_conversation_history_for_memory_exposure",
                side_effect=filter_then_edit,
            ),
        ):
            client, token = await self.start_broker()
            receipt = bound_ref(version=1)
            rejected = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers(token),
                json=request_payload(
                    history_ref=receipt,
                    messages=[
                        {
                            "role": "assistant",
                            "content": "PRIVATE_RACE_CANARY",
                            "memoryReceiptRef": receipt,
                        }
                    ],
                ),
            )
            rejected_payload = await rejected.json()
            self.assertEqual(rejected.status, 503, rejected_payload)
            self.assertEqual(
                rejected_payload["error"],
                "memory_deletion_journal_integrity_failed",
            )
            self.assertEqual(upstream_calls, 0)
            self.assertNotIn(
                "PRIVATE_RACE_CANARY",
                json.dumps(rejected_payload),
            )

        with (
            patch.object(
                broker,
                "MINDCRAFT_LOCAL_LLM_URL",
                str(upstream_server.make_url("/local")),
            ),
            patch.object(broker, "MINDCRAFT_LLM_DELIVERY_TTL_SEC", 2.0),
            patch.object(
                broker,
                "MINDCRAFT_LLM_DISCONNECT_POLL_SEC",
                0.01,
            ),
        ):
            client, token = await self.start_broker()
            receipt = bound_ref(version=2)
            response = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers(token),
                json=request_payload(
                    history_ref=receipt,
                    messages=[
                        {
                            "role": "assistant",
                            "content": "previous answer",
                            "memoryReceiptRef": receipt,
                        }
                    ],
                ),
            )
            frame = json.loads(
                (await response.content.readline()).decode("utf-8")
            )
            self.assertEqual(response.status, 200, frame)
            response.close()

            def edit_after_disconnect() -> None:
                with journal.memory_deletion_journal_guard(
                    index_dir,
                    require_stable=True,
                ):
                    write_memory_version(index_dir, 3)

            for _attempt in range(100):
                try:
                    await asyncio.to_thread(edit_after_disconnect)
                    break
                except journal.MemoryDeletionJournalBusyError:
                    await asyncio.sleep(0.02)
            else:
                self.fail("broker lease survived a disconnected delivery")
            self.assertEqual(upstream_calls, 1)

    async def test_stale_history_and_ack_timeout_are_terminal(self) -> None:
        index_dir = self.root / "memory_index"
        write_memory_version(index_dir, 1)
        seed_journal(index_dir)
        upstream_calls = 0

        async def upstream(_request: web.Request) -> web.Response:
            nonlocal upstream_calls
            upstream_calls += 1
            return web.json_response(
                {"choices": [{"message": {"content": "result"}}]}
            )

        upstream_app = web.Application()
        upstream_app.router.add_post("/local", upstream)
        upstream_server = await self.start_server(upstream_app)
        with (
            patch.object(
                broker,
                "MINDCRAFT_LOCAL_LLM_URL",
                str(upstream_server.make_url("/local")),
            ),
            patch.object(broker, "MINDCRAFT_LLM_DELIVERY_TTL_SEC", 0.05),
        ):
            client, token = await self.start_broker()
            stale = bound_ref(version=0)
            rejected = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers(token),
                json=request_payload(
                    history_ref=stale,
                    messages=[
                        {
                            "role": "assistant",
                            "content": "PRIVATE_STALE_HISTORY_CANARY",
                            "memoryReceiptRef": stale,
                        }
                    ],
                ),
            )
            rejected_payload = await rejected.json()
            self.assertEqual(rejected.status, 409, rejected_payload)
            self.assertEqual(
                rejected_payload["error"],
                "mindcraft_llm_history_stale",
            )
            self.assertNotIn(
                "PRIVATE_STALE_HISTORY_CANARY",
                json.dumps(rejected_payload),
            )
            self.assertEqual(upstream_calls, 0)

            response = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers(token),
                json=request_payload(),
            )
            frame = json.loads(
                (await response.content.readline()).decode("utf-8")
            )
            await asyncio.wait_for(response.read(), timeout=1)
            late = await client.post(
                "/internal/mindcraft-llm/ack",
                headers=self.headers(token),
                json={
                    "schema": broker.MINDCRAFT_LLM_DELIVERY_ACK_SCHEMA,
                    "requestId": frame["requestId"],
                    "leaseId": frame["deliveryLease"]["leaseId"],
                    "outcome": "delivered",
                    "contentFree": True,
                },
            )
            self.assertEqual(late.status, 409)
            self.assertEqual(upstream_calls, 1)

    async def test_qwen_admission_is_bounded_fifo_and_skips_disconnected_waiter(
        self,
    ) -> None:
        order: list[str] = []
        active = 0
        max_active = 0
        markers = ("first", "second", "third", "fourth", "replacement")
        entered = {marker: asyncio.Event() for marker in markers}
        release = {marker: asyncio.Event() for marker in markers}

        async def upstream(request: web.Request) -> web.Response:
            nonlocal active, max_active
            payload = await request.json()
            marker = payload["messages"][-1]["content"]
            order.append(marker)
            active += 1
            max_active = max(max_active, active)
            entered[marker].set()
            try:
                await release[marker].wait()
                return web.json_response(
                    {"choices": [{"message": {"content": marker}}]}
                )
            finally:
                active -= 1

        upstream_app = web.Application()
        upstream_app.router.add_post("/local", upstream)
        upstream_server = await self.start_server(upstream_app)
        with (
            patch.object(
                broker,
                "MINDCRAFT_LOCAL_LLM_URL",
                str(upstream_server.make_url("/local")),
            ),
            patch.object(broker, "MINDCRAFT_LLM_DISCONNECT_POLL_SEC", 0.01),
        ):
            client, token = await self.start_broker()

            def start(marker: str, kind: str) -> asyncio.Task:
                return asyncio.create_task(
                    client.post(
                        "/internal/mindcraft-llm",
                        headers=self.headers(token),
                        json=request_payload(
                            kind=kind,
                            messages=[
                                {
                                    "role": "user",
                                    "content": marker,
                                    "memoryReceiptRef": NOT_USED_REF,
                                }
                            ],
                        ),
                    )
                )

            async def wait_for_waiters(count: int) -> None:
                owner = client.server.app[broker._QWEN_ADMISSION]
                for _attempt in range(100):
                    if len(owner._waiters) == count:
                        return
                    await asyncio.sleep(0.01)
                self.fail(f"expected {count} Qwen waiters")

            tasks: dict[str, asyncio.Task] = {}
            try:
                tasks["first"] = start("first", "task")
                await asyncio.wait_for(entered["first"].wait(), timeout=1)
                tasks["second"] = start("second", "chat")
                await wait_for_waiters(1)
                tasks["third"] = start("third", "specialist")
                await wait_for_waiters(2)
                tasks["fourth"] = start("fourth", "task")
                await wait_for_waiters(3)

                busy = await client.post(
                    "/internal/mindcraft-llm",
                    headers=self.headers(token),
                    json=request_payload(kind="chat"),
                )
                self.assertEqual(busy.status, 503)
                self.assertEqual(
                    (await busy.json())["error"],
                    "mindcraft_llm_broker_busy",
                )

                tasks["second"].cancel()
                with suppress(asyncio.CancelledError):
                    await tasks["second"]
                await wait_for_waiters(2)
                tasks["replacement"] = start("replacement", "chat")
                await wait_for_waiters(3)

                for marker in ("first", "third", "fourth", "replacement"):
                    release[marker].set()
                    response = await asyncio.wait_for(tasks[marker], timeout=2)
                    frame = json.loads(
                        (await response.content.readline()).decode("utf-8")
                    )
                    self.assertEqual(frame["content"], marker)
                    await self.complete(client, response, frame, token)
                    if marker != "replacement":
                        next_marker = {
                            "first": "third",
                            "third": "fourth",
                            "fourth": "replacement",
                        }[marker]
                        await asyncio.wait_for(
                            entered[next_marker].wait(),
                            timeout=1,
                        )
            finally:
                for event in release.values():
                    event.set()
                for task in tasks.values():
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks.values(), return_exceptions=True)

        self.assertEqual(order, ["first", "third", "fourth", "replacement"])
        self.assertFalse(entered["second"].is_set())
        self.assertEqual(max_active, 1)

    async def test_qwen_queue_wait_does_not_spend_inference_timeout(self) -> None:
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        order: list[str] = []

        async def upstream(request: web.Request) -> web.Response:
            payload = await request.json()
            marker = payload["messages"][-1]["content"]
            order.append(marker)
            if marker == "first":
                first_entered.set()
                await release_first.wait()
            return web.json_response(
                {"choices": [{"message": {"content": marker}}]}
            )

        upstream_app = web.Application()
        upstream_app.router.add_post("/local", upstream)
        upstream_server = await self.start_server(upstream_app)
        with (
            patch.object(
                broker,
                "MINDCRAFT_LOCAL_LLM_URL",
                str(upstream_server.make_url("/local")),
            ),
            patch.object(broker, "MINDCRAFT_TASK_TIMEOUT_SEC", 0.01),
            patch.object(broker, "MINDCRAFT_LLM_DISCONNECT_POLL_SEC", 0.005),
        ):
            client, token = await self.start_broker()
            first_payload = request_payload(
                kind="chat",
                messages=[
                    {
                        "role": "user",
                        "content": "first",
                        "memoryReceiptRef": NOT_USED_REF,
                    }
                ],
            )
            second_payload = request_payload(
                kind="task",
                messages=[
                    {
                        "role": "user",
                        "content": "second",
                        "memoryReceiptRef": NOT_USED_REF,
                    }
                ],
            )
            first_task = asyncio.create_task(
                client.post(
                    "/internal/mindcraft-llm",
                    headers=self.headers(token),
                    json=first_payload,
                )
            )
            await asyncio.wait_for(first_entered.wait(), timeout=1)
            started = asyncio.get_running_loop().time()
            second_task = asyncio.create_task(
                client.post(
                    "/internal/mindcraft-llm",
                    headers=self.headers(token),
                    json=second_payload,
                )
            )
            await asyncio.sleep(0.05)
            release_first.set()

            first_response = await asyncio.wait_for(first_task, timeout=2)
            first_frame = json.loads(
                (await first_response.content.readline()).decode("utf-8")
            )
            await self.complete(client, first_response, first_frame, token)
            second_response = await asyncio.wait_for(second_task, timeout=2)
            second_frame = json.loads(
                (await second_response.content.readline()).decode("utf-8")
            )
            elapsed = asyncio.get_running_loop().time() - started
            self.assertEqual(second_response.status, 200, second_frame)
            self.assertEqual(second_frame["content"], "second")
            self.assertGreater(elapsed, 0.04)
            await self.complete(client, second_response, second_frame, token)

        self.assertEqual(order, ["first", "second"])

    async def test_qwen_grant_rechecks_expired_and_disconnected_waiters(
        self,
    ) -> None:
        class Transport:
            closing = False

            def is_closing(self) -> bool:
                return self.closing

        class Request:
            def __init__(self, transport: Transport) -> None:
                self.transport = transport

        async def wait_until_queued(owner: broker._QwenAdmissionOwner) -> None:
            for _attempt in range(100):
                if owner._waiters:
                    await asyncio.sleep(0)
                    return
                await asyncio.sleep(0.001)
            self.fail("Qwen waiter was not queued")

        transport = Transport()
        request = Request(transport)
        with patch.object(broker, "QWEN_ADMISSION_QUEUE_TIMEOUT_SEC", 0.01):
            owner = broker._QwenAdmissionOwner(max_waiters=1)
            active = await owner.acquire(request)  # type: ignore[arg-type]
            expired = asyncio.create_task(
                owner.acquire(request)  # type: ignore[arg-type]
            )
            await wait_until_queued(owner)
            time.sleep(0.03)
            await owner.release(active)
            with self.assertRaisesRegex(
                broker._RequestError,
                "^qwen_admission_queue_timeout$",
            ):
                await expired
            self.assertIsNone(owner._owner)

            active = await owner.acquire(request)  # type: ignore[arg-type]
            disconnected = asyncio.create_task(
                owner.acquire(request)  # type: ignore[arg-type]
            )
            await wait_until_queued(owner)
            transport.closing = True
            await owner.release(active)
            with self.assertRaisesRegex(
                ConnectionResetError,
                "^qwen_admission_client_disconnected$",
            ):
                await disconnected
            self.assertIsNone(owner._owner)

    async def test_inflight_cancel_keeps_slot_and_discards_late_result(self) -> None:
        first_entered = asyncio.Event()
        second_entered = asyncio.Event()
        release_first = asyncio.Event()
        active = 0
        max_active = 0

        async def upstream(request: web.Request) -> web.Response:
            nonlocal active, max_active
            payload = await request.json()
            marker = payload["messages"][-1]["content"]
            active += 1
            max_active = max(max_active, active)
            try:
                if marker == "first":
                    first_entered.set()
                    await release_first.wait()
                    content = "late-first"
                else:
                    second_entered.set()
                    content = "second-result"
                return web.json_response(
                    {"choices": [{"message": {"content": content}}]}
                )
            finally:
                active -= 1

        upstream_app = web.Application()
        upstream_app.router.add_post("/local", upstream)
        upstream_server = await self.start_server(upstream_app)
        with (
            patch.object(
                broker,
                "MINDCRAFT_LOCAL_LLM_URL",
                str(upstream_server.make_url("/local")),
            ),
            patch.object(broker, "MINDCRAFT_LLM_DISCONNECT_POLL_SEC", 0.01),
        ):
            client, token = await self.start_broker()
            first = request_payload(
                kind="chat",
                messages=[
                    {
                        "role": "user",
                        "content": "first",
                        "memoryReceiptRef": NOT_USED_REF,
                    }
                ],
            )
            second = request_payload(
                kind="specialist",
                messages=[
                    {
                        "role": "user",
                        "content": "second",
                        "memoryReceiptRef": NOT_USED_REF,
                    }
                ],
            )
            first_task = asyncio.create_task(
                client.post(
                    "/internal/mindcraft-llm",
                    headers=self.headers(token),
                    json=first,
                )
            )
            await asyncio.wait_for(first_entered.wait(), timeout=1)
            first_task.cancel()
            with suppress(asyncio.CancelledError):
                await first_task
            second_task = asyncio.create_task(
                client.post(
                    "/internal/mindcraft-llm",
                    headers=self.headers(token),
                    json=second,
                )
            )
            await asyncio.sleep(0.05)
            self.assertFalse(second_entered.is_set())
            self.assertEqual(active, 1)

            release_first.set()
            await asyncio.wait_for(second_entered.wait(), timeout=2)
            response = await asyncio.wait_for(second_task, timeout=2)
            frame = json.loads(
                (await response.content.readline()).decode("utf-8")
            )
            self.assertEqual(frame["requestId"], second["requestId"])
            self.assertEqual(frame["content"], "second-result")
            self.assertNotIn("late-first", json.dumps(frame))
            await self.complete(client, response, frame, token)

        self.assertEqual(max_active, 1)

    async def test_python_client_validates_consumption_before_delivery_ack(
        self,
    ) -> None:
        outcomes: list[str] = []

        async def upstream(request: web.Request) -> web.Response:
            payload = await request.json()
            marker = payload["messages"][-1]["content"]
            content = '{"type":"final"}' if marker == "valid" else "invalid"
            return web.json_response(
                {"choices": [{"message": {"content": content}}]}
            )

        original_wait = broker._wait_for_delivery

        async def observe_delivery(*args: object, **kwargs: object) -> str:
            result = await original_wait(*args, **kwargs)
            outcomes.append(result)
            return result

        upstream_app = web.Application()
        upstream_app.router.add_post("/local", upstream)
        upstream_server = await self.start_server(upstream_app)
        with (
            patch.object(
                broker,
                "MINDCRAFT_LOCAL_LLM_URL",
                str(upstream_server.make_url("/local")),
            ),
            patch.object(broker, "_wait_for_delivery", side_effect=observe_delivery),
        ):
            client, _token = await self.start_broker()
            endpoint = str(
                client.server.make_url("/internal/mindcraft-llm")
            )
            async with ClientSession() as session:
                result = await broker.request_mindcraft_llm_from_broker(
                    session=session,
                    broker_url=endpoint,
                    token_file=self.token_path,
                    request_kind="task",
                    messages=[{"role": "user", "content": "valid"}],
                    expected_memory_exposure=None,
                    memory_index_dir=self.root / "memory_index",
                    inference_timeout_sec=1,
                    consume=json.loads,
                )
                self.assertEqual(result, {"type": "final"})
                with self.assertRaises(json.JSONDecodeError):
                    await broker.request_mindcraft_llm_from_broker(
                        session=session,
                        broker_url=endpoint,
                        token_file=self.token_path,
                        request_kind="task",
                        messages=[{"role": "user", "content": "invalid"}],
                        expected_memory_exposure=None,
                        memory_index_dir=self.root / "memory_index",
                        inference_timeout_sec=1,
                        consume=json.loads,
                    )

        self.assertEqual(outcomes, ["delivered", "discarded"])

    async def test_python_client_rejects_wrong_request_id_without_consuming(
        self,
    ) -> None:
        consumed = False

        async def wrong_id(request: web.Request) -> web.Response:
            payload = await request.json()
            frame = {
                "schema": broker.MINDCRAFT_LLM_RESULT_SCHEMA,
                "requestId": str(uuid.uuid4()),
                "content": "wrong",
                "memoryReceiptRef": NOT_USED_REF,
                "deliveryLease": {
                    "schema": broker.MINDCRAFT_LLM_DELIVERY_LEASE_SCHEMA,
                    "leaseId": "a" * 64,
                    "ttlMs": 1000,
                    "contentFree": True,
                },
            }
            self.assertNotEqual(frame["requestId"], payload["requestId"])
            return web.Response(
                text=json.dumps(frame) + "\n",
                content_type="application/x-ndjson",
            )

        rogue_app = web.Application()
        rogue_app.router.add_post("/internal/mindcraft-llm", wrong_id)
        rogue_server = await self.start_server(rogue_app)
        self.token_path.write_text("x" * 64, encoding="utf-8")

        def consume(_content: str) -> str:
            nonlocal consumed
            consumed = True
            return "unsafe"

        async with ClientSession() as session:
            with self.assertRaisesRegex(
                RuntimeError,
                "^mindcraft_llm_broker_frame_invalid$",
            ):
                await broker.request_mindcraft_llm_from_broker(
                    session=session,
                    broker_url=str(
                        rogue_server.make_url("/internal/mindcraft-llm")
                    ),
                    token_file=self.token_path,
                    request_kind="task",
                    messages=[{"role": "user", "content": "hello"}],
                    expected_memory_exposure=None,
                    memory_index_dir=self.root / "memory_index",
                    inference_timeout_sec=1,
                    consume=consume,
                )
        self.assertFalse(consumed)

    async def test_python_client_rejects_wrong_receipt_without_consuming(
        self,
    ) -> None:
        consumed = False

        async def wrong_receipt(request: web.Request) -> web.Response:
            payload = await request.json()
            frame = {
                "schema": broker.MINDCRAFT_LLM_RESULT_SCHEMA,
                "requestId": payload["requestId"],
                "content": "wrong receipt",
                "memoryReceiptRef": bound_ref(),
                "deliveryLease": {
                    "schema": broker.MINDCRAFT_LLM_DELIVERY_LEASE_SCHEMA,
                    "leaseId": "a" * 64,
                    "ttlMs": 1000,
                    "contentFree": True,
                },
            }
            return web.Response(
                text=json.dumps(frame) + "\n",
                content_type="application/x-ndjson",
            )

        rogue_app = web.Application()
        rogue_app.router.add_post("/internal/mindcraft-llm", wrong_receipt)
        rogue_server = await self.start_server(rogue_app)
        self.token_path.write_text("x" * 64, encoding="utf-8")

        def consume(_content: str) -> str:
            nonlocal consumed
            consumed = True
            return "unsafe"

        async with ClientSession() as session:
            with self.assertRaisesRegex(
                RuntimeError,
                "^mindcraft_llm_broker_frame_invalid$",
            ):
                await broker.request_mindcraft_llm_from_broker(
                    session=session,
                    broker_url=str(
                        rogue_server.make_url("/internal/mindcraft-llm")
                    ),
                    token_file=self.token_path,
                    request_kind="task",
                    messages=[{"role": "user", "content": "hello"}],
                    expected_memory_exposure=None,
                    memory_index_dir=self.root / "memory_index",
                    inference_timeout_sec=1,
                    consume=consume,
                )
        self.assertFalse(consumed)

    async def test_client_bounds_trailing_and_ack_response_reads(self) -> None:
        async def valid_ack(_request: web.Request) -> web.Response:
            return web.json_response({"ok": True, "contentFree": True})

        async def oversized_ack(_request: web.Request) -> web.Response:
            return web.Response(body=b"x" * 4097)

        ack_app = web.Application()
        ack_app.router.add_post("/valid", valid_ack)
        ack_app.router.add_post("/oversized", oversized_ack)
        ack_server = await self.start_server(ack_app)

        class DummyResponse:
            def __init__(self, trailing: bytes) -> None:
                self.content = self
                self.trailing = trailing
                self.read_limits: list[int] = []
                self.closed = False

            async def read(self, limit: int) -> bytes:
                self.read_limits.append(limit)
                return self.trailing[:limit]

            def close(self) -> None:
                self.closed = True

        trailing = DummyResponse(b"x" * (1024 * 1024))
        with self.assertRaisesRegex(
            RuntimeError,
            "^mindcraft_llm_broker_frame_invalid$",
        ):
            await broker._complete_broker_delivery(
                None,
                trailing,
                ack_url=str(ack_server.make_url("/valid")),
                headers={},
                request_id=str(uuid.uuid4()),
                lease_id="a" * 64,
                outcome="delivered",
            )
        self.assertEqual(trailing.read_limits, [1])

        clean = DummyResponse(b"")
        with self.assertRaisesRegex(
            RuntimeError,
            "^mindcraft_llm_delivery_ack_failed$",
        ):
            await broker._complete_broker_delivery(
                None,
                clean,
                ack_url=str(ack_server.make_url("/oversized")),
                headers={},
                request_id=str(uuid.uuid4()),
                lease_id="b" * 64,
                outcome="delivered",
            )
        self.assertEqual(clean.read_limits, [1])

    async def test_guard_exit_failure_prevents_successful_delivery_ack(
        self,
    ) -> None:
        async def upstream(_request: web.Request) -> web.Response:
            return web.json_response(
                {"choices": [{"message": {"content": "result"}}]}
            )

        @contextmanager
        def unstable_guard(**_kwargs: object):
            yield None
            raise RuntimeError("memory_guard_changed")

        upstream_app = web.Application()
        upstream_app.router.add_post("/local", upstream)
        upstream_server = await self.start_server(upstream_app)
        with (
            patch.object(
                broker,
                "MINDCRAFT_LOCAL_LLM_URL",
                str(upstream_server.make_url("/local")),
            ),
            patch.object(broker, "memory_exposure_guard", unstable_guard),
        ):
            client, token = await self.start_broker()
            payload = request_payload(kind="action")
            response = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers(token),
                json=payload,
            )
            frame = json.loads(
                (await response.content.readline()).decode("utf-8")
            )
            lease = frame["deliveryLease"]
            ack_task = asyncio.create_task(
                client.post(
                    "/internal/mindcraft-llm/ack",
                    headers=self.headers(token),
                    json={
                        "schema": broker.MINDCRAFT_LLM_DELIVERY_ACK_SCHEMA,
                        "requestId": frame["requestId"],
                        "leaseId": lease["leaseId"],
                        "outcome": "delivered",
                        "contentFree": True,
                    },
                )
            )
            await asyncio.wait_for(response.read(), timeout=2)
            ack = await asyncio.wait_for(ack_task, timeout=2)
            self.assertEqual(ack.status, 503)
            self.assertEqual(
                (await ack.json())["error"],
                "mindcraft_llm_delivery_guard_failed",
            )

    async def test_router_guard_exit_failure_prevents_successful_delivery_ack(
        self,
    ) -> None:
        async def upstream(_request: web.Request) -> web.Response:
            return web.json_response(
                {"choices": [{"message": {"content": "route"}}]}
            )

        @contextmanager
        def unstable_guard(**_kwargs: object):
            yield None
            raise RuntimeError("memory_guard_changed")

        upstream_app = web.Application()
        upstream_app.router.add_post("/router", upstream)
        upstream_server = await self.start_server(upstream_app)
        with (
            patch.object(
                broker,
                "MINDCRAFT_ROUTER_LLM_URL",
                str(upstream_server.make_url("/router")),
            ),
            patch.object(broker, "memory_exposure_guard", unstable_guard),
        ):
            client, token = await self.start_broker()
            response = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers(token),
                json=request_payload(kind="router"),
            )
            frame = json.loads(
                (await response.content.readline()).decode("utf-8")
            )
            lease = frame["deliveryLease"]
            ack_task = asyncio.create_task(
                client.post(
                    "/internal/mindcraft-llm/ack",
                    headers=self.headers(token),
                    json={
                        "schema": broker.MINDCRAFT_LLM_DELIVERY_ACK_SCHEMA,
                        "requestId": frame["requestId"],
                        "leaseId": lease["leaseId"],
                        "outcome": "delivered",
                        "contentFree": True,
                    },
                )
            )
            await asyncio.wait_for(response.read(), timeout=2)
            ack = await asyncio.wait_for(ack_task, timeout=2)
            self.assertEqual(ack.status, 503)
            self.assertEqual(
                (await ack.json())["error"],
                "mindcraft_llm_delivery_guard_failed",
            )

    async def test_shutdown_poisons_and_drains_active_inference(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        cancelled = False

        async def upstream(_request: web.Request) -> web.Response:
            nonlocal cancelled
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled = True
                raise
            return web.json_response(
                {"choices": [{"message": {"content": "drained"}}]}
            )

        upstream_app = web.Application()
        upstream_app.router.add_post("/local", upstream)
        upstream_server = await self.start_server(upstream_app)
        with patch.object(
            broker,
            "MINDCRAFT_LOCAL_LLM_URL",
            str(upstream_server.make_url("/local")),
        ):
            client, token = await self.start_broker()
            request_task = asyncio.create_task(
                client.post(
                    "/internal/mindcraft-llm",
                    headers=self.headers(token),
                    json=request_payload(kind="action"),
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=1)
            marker = client.server.app[broker._QWEN_INFLIGHT_MARKER]
            self.assertTrue(marker.is_file())
            close_task = asyncio.create_task(client.close())
            await asyncio.sleep(0.05)
            self.assertFalse(close_task.done())
            self.assertFalse(
                client.server.app[broker._QWEN_ADMISSION].available
            )
            self.assertFalse(cancelled)
            self.assertTrue(marker.is_file())
            release.set()
            await asyncio.wait_for(close_task, timeout=2)
            await asyncio.gather(request_task, return_exceptions=True)
            self.assertFalse(cancelled)
            self.assertFalse(marker.exists())

    async def test_stale_marker_requires_new_healthy_qwen_epoch(self) -> None:
        healthy = asyncio.Event()

        async def health(_request: web.Request) -> web.Response:
            if healthy.is_set():
                return web.json_response({"status": "ok"})
            return web.json_response({"status": "loading"}, status=503)

        upstream_app = web.Application()
        upstream_app.router.add_get("/health", health)
        upstream_server = await self.start_server(upstream_app)
        self.token_path.with_name("qwen-inflight").write_text(
            "11111111-1111-4111-8111-111111111111\n",
            encoding="utf-8",
        )
        app = web.Application()
        broker.install_mindcraft_llm_broker(app)
        client = TestClient(TestServer(app))
        self.clients.append(client)
        with (
            patch.object(
                broker,
                "MINDCRAFT_LOCAL_LLM_URL",
                str(upstream_server.make_url("/v1/chat/completions")),
            ),
            patch.object(broker, "MINDCRAFT_LLM_DISCONNECT_POLL_SEC", 0.005),
        ):
            startup = asyncio.create_task(client.start_server())
            await asyncio.sleep(0.03)
            self.assertFalse(startup.done())
            self.epoch_path.write_text(
                "22222222-2222-4222-8222-222222222222\n",
                encoding="utf-8",
            )
            await asyncio.sleep(0.03)
            self.assertFalse(startup.done())
            healthy.set()
            await asyncio.wait_for(startup, timeout=2)
        self.assertFalse(
            self.token_path.with_name("qwen-inflight").exists()
        )

    async def test_client_total_budget_includes_delivery_ack(self) -> None:
        captured: list[float] = []
        failure = json.dumps(
            {
                "ok": False,
                "error": "qwen_admission_queue_timeout",
                "contentFree": True,
            }
        ).encode("utf-8")

        class FailureContent:
            async def read(self, _limit: int) -> bytes:
                return failure

        class FailureResponse:
            status = 503
            content = FailureContent()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

        class FailureSession:
            def post(self, *_args: object, **kwargs: object) -> FailureResponse:
                captured.append(kwargs["timeout"].total)
                return FailureResponse()

        self.token_path.write_text("x" * 64, encoding="utf-8")
        with self.assertRaisesRegex(
            TimeoutError,
            "^qwen_admission_queue_timeout$",
        ):
            await broker.request_mindcraft_llm_from_broker(
                session=FailureSession(),
                broker_url="http://127.0.0.1:8798/internal/mindcraft-llm",
                token_file=self.token_path,
                request_kind="task",
                messages=[{"role": "user", "content": "hello"}],
                expected_memory_exposure=None,
                memory_index_dir=self.root / "memory_index",
                inference_timeout_sec=2,
                queue_timeout_sec=1,
                consume=lambda content: content,
            )
        self.assertEqual(
            captured,
            [
                1
                + 2
                + broker.MINDCRAFT_LLM_CLIENT_ACK_TIMEOUT_SEC
                + broker.MINDCRAFT_LLM_CLIENT_GRACE_SEC
            ],
        )

    async def test_complete_malformed_upstream_response_is_invocation_local(
        self,
    ) -> None:
        calls = 0

        async def upstream(_request: web.Request) -> web.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return web.Response(body=b"not-json", status=200)
            return web.json_response(
                {"choices": [{"message": {"content": "recovered"}}]}
            )

        upstream_app = web.Application()
        upstream_app.router.add_post("/local", upstream)
        upstream_server = await self.start_server(upstream_app)
        with patch.object(
            broker,
            "MINDCRAFT_LOCAL_LLM_URL",
            str(upstream_server.make_url("/local")),
        ):
            client, token = await self.start_broker()
            malformed = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers(token),
                json=request_payload(),
            )
            self.assertEqual(malformed.status, 503)
            self.assertTrue(
                client.server.app[broker._QWEN_ADMISSION].available
            )
            self.assertFalse(
                client.server.app[broker._QWEN_INFLIGHT_MARKER].exists()
            )

            recovered = await client.post(
                "/internal/mindcraft-llm",
                headers=self.headers(token),
                json=request_payload(),
            )
            frame = json.loads(
                (await recovered.content.readline()).decode("utf-8")
            )
            self.assertEqual(frame["content"], "recovered")
            await self.complete(client, recovered, frame, token)

        self.assertEqual(calls, 2)

    async def test_content_free_health_uses_owner_only_upstream(self) -> None:
        health_calls = 0
        rotate_epoch = False

        async def health(_request: web.Request) -> web.Response:
            nonlocal health_calls, rotate_epoch
            health_calls += 1
            if rotate_epoch:
                self.epoch_path.write_text(
                    "33333333-3333-4333-8333-333333333333\n",
                    encoding="utf-8",
                )
            return web.json_response({"status": "ok"})

        upstream_app = web.Application()
        upstream_app.router.add_get("/health", health)
        upstream_server = await self.start_server(upstream_app)
        with patch.object(
            broker,
            "MINDCRAFT_LOCAL_LLM_URL",
            str(upstream_server.make_url("/v1/chat/completions")),
        ):
            client, _token = await self.start_broker()
            self.epoch_path.unlink()
            missing_epoch = await client.get("/internal/mindcraft-llm/health")
            self.assertEqual(missing_epoch.status, 503)
            self.epoch_path.write_text("corrupt\n", encoding="utf-8")
            corrupt_epoch = await client.get("/internal/mindcraft-llm/health")
            self.assertEqual(corrupt_epoch.status, 503)
            self.assertEqual(health_calls, 0)
            self.epoch_path.write_text(
                "22222222-2222-4222-8222-222222222222\n",
                encoding="utf-8",
            )
            marker = self.token_path.with_name("qwen-inflight")
            marker.write_text(
                "11111111-1111-4111-8111-111111111111\n",
                encoding="utf-8",
            )
            stale_marker = await client.get("/internal/mindcraft-llm/health")
            self.assertEqual(stale_marker.status, 503)
            self.assertEqual(health_calls, 0)
            marker.unlink()

            rotate_epoch = True
            changing_epoch = await client.get("/internal/mindcraft-llm/health")
            self.assertEqual(changing_epoch.status, 503)
            self.assertEqual(health_calls, 1)
            rotate_epoch = False
            ready = await client.get("/internal/mindcraft-llm/health")
            self.assertEqual(
                await ready.json(),
                {"ok": True, "ready": True, "contentFree": True},
            )
            self.assertEqual(health_calls, 2)
            marker.write_text(
                "33333333-3333-4333-8333-333333333333\n",
                encoding="utf-8",
            )
            active_marker = await client.get("/internal/mindcraft-llm/health")
            self.assertEqual(active_marker.status, 200)
            self.assertEqual(health_calls, 3)
            marker.unlink()
            await client.server.app[broker._QWEN_ADMISSION].poison()
            unavailable = await client.get("/internal/mindcraft-llm/health")
            self.assertEqual(unavailable.status, 503)
            self.assertEqual(health_calls, 3)
            self.assertEqual(
                (await unavailable.json())["error"],
                "qwen_admission_unavailable",
            )


if __name__ == "__main__":
    unittest.main()
