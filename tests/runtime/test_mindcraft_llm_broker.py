from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
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
        self.environment = patch.dict(
            os.environ,
            {
                broker.MINDCRAFT_LLM_TOKEN_FILE_ENV: str(
                    self.token_path
                ),
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


if __name__ == "__main__":
    unittest.main()
