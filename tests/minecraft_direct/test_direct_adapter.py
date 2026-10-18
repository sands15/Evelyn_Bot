"""Synthetic loopback only; do not import the Evelyn application runtime."""
import asyncio
import importlib.util
import json
from pathlib import Path
import unittest

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "minecraft_direct_adapter",
    ROOT / "evelyn_core/runtime/evelyn_core/minecraft_direct_adapter.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
DirectAdapter, AdapterError = module.DirectAdapter, module.AdapterError
TOKEN = "synthetic-test-token-" + "a" * 32


def hello():
    return {"v": 1, "type": "hello", "session": "s1", "world": "w1",
            "incarnation": "i1", "next_seq": 1,
            "observation": {"seq": 1, "tick": 1, "state": "ready"},
            "capabilities": ["probe", "hold"], "active": None, "receipts": []}


def receipt(request, status="accepted", code="accepted", next_seq=2):
    return {"v": 1, "type": "receipt", "session": "s1", "world": "w1",
            "request_id": request["request_id"], "seq": request["seq"],
            "status": status, "code": code, "next_seq": next_seq}


class DirectAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = DirectAdapter(TOKEN)
        self.url = await self.adapter.start()
        self.http = aiohttp.ClientSession()
        self.peers = []

    async def asyncTearDown(self):
        for peer in self.peers:
            await peer.close()
        await self.http.close()
        await self.adapter.close()

    async def peer(self, frame=None):
        peer = await self.http.ws_connect(self.url, headers={"Authorization": "Bearer " + TOKEN})
        self.peers.append(peer)
        await peer.send_json(frame or hello())
        self.assertEqual((await peer.receive_json())["type"], "welcome")
        await self.adapter.wait_connected(1)
        return peer

    async def next_request(self, peer):
        while True:
            message = await peer.receive_json(timeout=2)
            if message["type"] != "heartbeat":
                return message
            await peer.send_json({"v": 1, "type": "heartbeat", "session": "s1", "world": "w1"})

    async def test_auth_origin_query_rejected(self):
        for url, headers in [(self.url, {}),
                             (self.url, {"Authorization": "Bearer wrong"}),
                             (self.url, {"Authorization": "Bearer " + TOKEN, "Origin": "http://localhost"}),
                             (self.url + "?secret=no", {"Authorization": "Bearer " + TOKEN})]:
            with self.assertRaises(aiohttp.WSServerHandshakeError):
                await self.http.ws_connect(url, headers=headers)

    async def test_duplicate_keys_and_wrong_version_close(self):
        for frame in ['{"v":1,"v":1,"type":"hello"}', json.dumps({**hello(), "v": True})]:
            peer = await self.http.ws_connect(self.url, headers={"Authorization": "Bearer " + TOKEN})
            await peer.send_str(frame)
            await peer.receive(timeout=2)
            self.assertTrue(peer.closed or (await peer.receive(timeout=2)).type in
                            {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED})
            await peer.close()
            await asyncio.sleep(0)

    async def test_command_and_non_consuming_query(self):
        peer = await self.peer()
        call = asyncio.create_task(self.adapter.command("probe"))
        request = await self.next_request(peer)
        self.assertEqual(request["seq"], 1)
        await peer.send_json(receipt(request))
        self.assertEqual((await call)["status"], "accepted")
        for _ in range(2):
            query = asyncio.create_task(self.adapter.query(1, session="s1", world="w1"))
            request = await self.next_request(peer)
            await peer.send_json(receipt(request, "succeeded", "completed"))
            self.assertEqual((await query)["status"], "succeeded")

    async def test_unsolicited_receipt_cannot_complete_request(self):
        peer = await self.peer()
        call = asyncio.create_task(self.adapter.command("hold"))
        request = await self.next_request(peer)
        await peer.send_json({**receipt(request, "running", "running"), "request_id": None})
        await asyncio.sleep(0.01)
        self.assertFalse(call.done())
        await peer.send_json(receipt(request))
        await call

    async def test_cancel_interrupts_outstanding_query(self):
        peer = await self.peer()
        query = asyncio.create_task(self.adapter.query(1, session="s1", world="w1"))
        first = await self.next_request(peer)
        cancel = asyncio.create_task(self.adapter.cancel(1, session="s1", world="w1"))
        second = await self.next_request(peer)
        self.assertEqual(second["type"], "cancel")
        await peer.send_json(receipt(second, "cancelled", "cancelled"))
        self.assertEqual((await cancel)["status"], "cancelled")
        self.assertFalse(query.done())
        await peer.send_json(receipt(first, "cancelled", "cancelled"))
        await query

    async def test_timeout_blocks_new_command_until_resync(self):
        peer = await self.peer()
        call = asyncio.create_task(self.adapter.command("probe", timeout=0.03))
        await self.next_request(peer)
        with self.assertRaisesRegex(AdapterError, "outcome_unknown"):
            await call
        with self.assertRaisesRegex(AdapterError, "resync_required"):
            await self.adapter.command("probe")
        # Only an authoritative hello can resolve sequence uncertainty.
        await peer.send_json({**hello(), "next_seq": 2})
        await self.next_request(peer)
        call = asyncio.create_task(self.adapter.command("probe"))
        request = await self.next_request(peer)
        self.assertEqual(request["seq"], 2)
        await peer.send_json(receipt(request, next_seq=3))
        await call

    async def test_disconnect_resolves_pending_as_unknown(self):
        peer = await self.peer()
        call = asyncio.create_task(self.adapter.command("hold"))
        await self.next_request(peer)
        await peer.close()
        with self.assertRaisesRegex(AdapterError, "outcome_unknown"):
            await call

    async def test_old_binding_rejected_after_new_peer(self):
        peer = await self.peer()
        await peer.close()
        await asyncio.sleep(0.01)
        await self.peer({**hello(), "session": "s2", "incarnation": "i2"})
        with self.assertRaisesRegex(AdapterError, "stale_binding"):
            await self.adapter.cancel(1, session="s1", world="w1")

    async def test_second_peer_rejected_and_snapshot_copy(self):
        await self.peer()
        with self.assertRaises(aiohttp.WSServerHandshakeError):
            await self.http.ws_connect(self.url, headers={"Authorization": "Bearer " + TOKEN})
        state = self.adapter.snapshot()
        state["observation"]["state"] = "tampered"
        self.assertEqual(self.adapter.snapshot()["observation"]["state"], "ready")

    async def test_oversized_binary_and_unknown_fields_close(self):
        for mode in ("large", "binary", "extra"):
            peer = await self.peer()
            if mode == "large":
                await peer.send_str("x" * 32769)
            elif mode == "binary":
                await peer.send_bytes(b"x")
            else:
                await peer.send_json({"v": 1, "type": "heartbeat", "session": "s1", "world": "w1", "extra": 1})
            await peer.receive(timeout=2)
            await peer.close()
            await asyncio.sleep(0.01)

    async def test_request_cancellation_does_not_retry(self):
        peer = await self.peer()
        call = asyncio.create_task(self.adapter.command("probe"))
        await self.next_request(peer)
        call.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await call
        self.assertEqual(self.adapter.pending_count, 0)
        with self.assertRaisesRegex(AdapterError, "resync_required"):
            await self.adapter.command("probe")

    async def test_duplicate_rejection_preserves_original_running(self):
        peer = await self.peer()
        call = asyncio.create_task(self.adapter.command("hold"))
        request = await self.next_request(peer)
        await peer.send_json(receipt(request, "running", "running"))
        await call
        await peer.send_json({**receipt(request, "rejected", "sequence_conflict"), "request_id": "late"})
        await asyncio.sleep(0.01)
        self.assertEqual(self.adapter.snapshot()["active"]["status"], "running")

    async def test_plain_http_bad_upgrade_does_not_reserve_peer(self):
        async with self.http.get(self.url.replace("ws:", "http:"), headers={"Authorization": "Bearer " + TOKEN}) as response:
            self.assertEqual(response.status, 400)
        await self.peer()

    async def test_heartbeat_loss_closes_and_shutdown_releases_listener(self):
        peer = await self.peer()
        await asyncio.sleep(3.1)
        self.assertFalse(self.adapter.snapshot()["connected"])
        await peer.close()
        await self.adapter.close()
        with self.assertRaises(aiohttp.ClientConnectorError):
            await self.http.ws_connect(self.url, timeout=aiohttp.ClientWSTimeout(ws_close=0.1))


    async def test_cancelled_cannot_regress_on_late_command_ack(self):
        peer = await self.peer()
        command = asyncio.create_task(self.adapter.command("hold"))
        original = await self.next_request(peer)
        cancel = asyncio.create_task(self.adapter.cancel(1, session="s1", world="w1"))
        stop = await self.next_request(peer)
        await peer.send_json(receipt(stop, "cancelled", "cancelled"))
        await cancel
        await peer.send_json(receipt(original))
        self.assertEqual((await command)["status"], "cancelled")
        self.assertIsNone(self.adapter.snapshot()["active"])
        self.assertEqual(self.adapter.snapshot()["receipts"][-1]["status"], "cancelled")

    async def test_exact_terminal_query_recovers_timeout(self):
        peer = await self.peer()
        command = asyncio.create_task(self.adapter.command("probe", timeout=0.02))
        await self.next_request(peer)
        with self.assertRaisesRegex(AdapterError, "outcome_unknown"):
            await command
        query = asyncio.create_task(self.adapter.query(1, session="s1", world="w1"))
        request = await self.next_request(peer)
        await peer.send_json(receipt(request, "succeeded", "completed"))
        await query
        self.assertFalse(self.adapter.snapshot()["resync_required"])
        command = asyncio.create_task(self.adapter.command("probe"))
        request = await self.next_request(peer)
        self.assertEqual(request["seq"], 2)
        await peer.send_json(receipt(request, next_seq=3))
        await command

    async def test_old_failure_cannot_poison_new_resync(self):
        peer = await self.peer()
        command = asyncio.create_task(self.adapter.command("hold"))
        await self.next_request(peer)
        await peer.send_json({**hello(), "world": "w2", "next_seq": 2})
        self.assertEqual((await self.next_request(peer))["type"], "welcome")
        with self.assertRaisesRegex(AdapterError, "outcome_unknown"):
            await command
        self.assertEqual(self.adapter.snapshot()["world"], "w2")
        self.assertFalse(self.adapter.snapshot()["resync_required"])

    async def test_different_cancel_timeout_cannot_erase_unknown_command(self):
        peer = await self.peer({**hello(), "next_seq": 2,
                                "receipts": [{"seq": 1, "status": "succeeded", "code": "completed"}]})
        command = asyncio.create_task(self.adapter.command("probe", timeout=0.03))
        await self.next_request(peer)
        cancel = asyncio.create_task(self.adapter.cancel(1, session="s1", world="w1", timeout=0.07))
        await self.next_request(peer)
        for pending in (command, cancel):
            with self.assertRaisesRegex(AdapterError, "outcome_unknown"):
                await pending
        for seq in (1, 2):
            query = asyncio.create_task(self.adapter.query(seq, session="s1", world="w1"))
            request = await self.next_request(peer)
            await peer.send_json(receipt(request, "succeeded", "completed", next_seq=3))
            await query
            self.assertEqual(self.adapter.snapshot()["resync_required"], seq == 1)
            if seq == 1:
                with self.assertRaisesRegex(AdapterError, "resync_required"):
                    await self.adapter.command("probe")

    async def test_uncertainty_storage_is_bounded_and_overflow_requires_hello(self):
        peer = await self.peer()
        for seq in range(1, 66):
            cancel = asyncio.create_task(self.adapter.cancel(seq, session="s1", world="w1", timeout=0.01))
            await self.next_request(peer)
            with self.assertRaisesRegex(AdapterError, "outcome_unknown"):
                await cancel
        self.assertLessEqual(len(self.adapter._uncertain_requests), 64)
        for seq in range(1, 66):
            query = asyncio.create_task(self.adapter.query(seq, session="s1", world="w1"))
            request = await self.next_request(peer)
            await peer.send_json(receipt(request, "succeeded", "completed", next_seq=66))
            await query
        self.assertTrue(self.adapter.snapshot()["resync_required"])
        await peer.send_json({**hello(), "next_seq": 66})
        self.assertEqual((await self.next_request(peer))["type"], "welcome")
        await asyncio.sleep(0)
        self.assertFalse(self.adapter.snapshot()["resync_required"])


if __name__ == "__main__":
    attempt_spec = importlib.util.spec_from_file_location("_evelyn_test_attempt_capture", ROOT / "tools/run_test_attempt.py")
    attempt_capture = importlib.util.module_from_spec(attempt_spec)
    attempt_spec.loader.exec_module(attempt_capture)
    attempt_capture.install_unittest_capture()
    unittest.main()
