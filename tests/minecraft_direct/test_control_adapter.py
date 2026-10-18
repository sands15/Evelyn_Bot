"""Strict v3 local-control contract; owned loopback only, no game or authentication."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import unittest

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("control_direct", ROOT / "evelyn_core/runtime/evelyn_core/minecraft_direct_adapter.py")
direct = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(direct)
TOKEN = "control-synthetic-" + "x" * 32
CONNECTION = "00000000-0000-4000-8000-000000000001"
WORLD = CONNECTION + ":1"


def observation():
    player = {"seq": 2, "connection_id": CONNECTION, "generation": 1, "event_seq": 3,
              "observed_at_ms": 50, "source": "server_observed", "available": True,
              "dimension": "minecraft:overworld", "position": {
                  "x": 1.5, "y": 64., "z": -2.5, "yaw": 90., "pitch": 0., "source": "server_correction"},
              "health": {"health": 20., "food": 20, "saturation": 5.}, "world_ready": False,
              "coverage": {"chunks": "unknown", "entities": "unknown", "inventory": "unknown"}}
    return {"seq": 7, "sampled_at_ms": 100, "source": "local_control", "self": player,
            "motion": {"x": 1.6, "y": 64., "z": -2.5, "yaw": 90., "pitch": 0.,
                       "vx": .1, "vy": 0., "vz": 0., "epoch": 1, "source": "client_simulated"},
            "planner": {"status": "idle", "code": "idle", "goal_seq": 0, "step_id": 0, "paused": False, "preemptions": 0},
            "risk": {"damage_epoch": 2, "monster_epoch": 3, "near_monster": False,
                     "coverage": "server_tracked_partial", "tracked_count": 4}, "enabled": False}


def hello():
    return {"v": 3, "type": "hello", "session": "s1", "world": WORLD, "incarnation": "i1",
            "next_seq": 1, "observation": observation(), "capabilities": ["look", "navigate"], "active": None, "receipts": []}


def receipt(command, status="succeeded", code="done", evidence=None):
    return {"v": 3, "type": "receipt", "session": command["session"], "world": command["world"],
            "request_id": command["request_id"], "seq": command["seq"], "status": status, "code": code,
            "next_seq": command["seq"] + 1, "evidence": evidence or ("client_simulated" if status == "succeeded" else "none")}


class ControlSchemaTests(unittest.TestCase):
    def test_explicit_v3_valid_shape_and_initial_unknown(self):
        adapter = direct.DirectAdapter(TOKEN, protocol_version=3)
        direct._validate(hello(), 3)
        initial = hello(); initial.update(observation=None, world="none")
        direct._validate(initial, 3)
        unknown = hello(); unknown["observation"]["self"].update(available=False, dimension=None, position=None, health=None)
        unknown["observation"].update(motion=None, enabled=False)
        direct._validate(unknown, 3)
        self.assertEqual(adapter.snapshot()["received_age_ms"], None)

    def test_strict_shapes_sources_evidence_and_numbers(self):
        mutations = [lambda f: f.update(v=2), lambda f: f.update(world=CONNECTION + ":2"),
                     lambda f: f.update(capabilities=["navigate"]), lambda f: f.update(capabilities=["navigate", "look"]),
                     lambda f: f["observation"].update(seq=0), lambda f: f["observation"].update(sampled_at_ms=-1),
                     lambda f: f["observation"].update(source="server_observed"), lambda f: f["observation"].update(enabled=1),
                     lambda f: f["observation"]["motion"].update(source="server_observed"),
                     lambda f: f["observation"]["motion"].update(x=float("nan")),
                     lambda f: f["observation"]["motion"].update(vx=10 ** 400),
                     lambda f: f["observation"]["motion"].update(vy=True),
                     lambda f: f["observation"]["motion"].update(pitch=91),
                     lambda f: f["observation"]["motion"].update(epoch=-1),
                     lambda f: f["observation"]["planner"].update(goal_seq=True),
                     lambda f: f["observation"]["planner"].update(paused="false"),
                     lambda f: f["observation"]["planner"].update(extra=0),
                     lambda f: f["observation"]["risk"].update(damage_epoch=-1),
                     lambda f: f["observation"]["risk"].update(monster_epoch=True),
                     lambda f: f["observation"]["risk"].update(near_monster=0),
                     lambda f: f["observation"]["risk"].update(coverage="complete"),
                     lambda f: f["observation"]["risk"].update(tracked_count=1025),
                     lambda f: f["observation"]["self"].update(position=None),
                     lambda f: f["observation"]["self"].update(source="client_simulated")]
        for index, mutate in enumerate(mutations):
            bad = hello(); mutate(bad)
            with self.subTest(index=index), self.assertRaises(direct.AdapterError): direct._validate(bad, 3)
        base = {"session": "s1", "world": WORLD, "request_id": "r1", "seq": 1}
        for status in direct.STATUSES:
            good = receipt(base, status)
            direct._validate(good, 3)
            bad = dict(good, evidence="none" if status == "succeeded" else "client_simulated")
            with self.assertRaises(direct.AdapterError): direct._validate(bad, 3)
            bad = dict(good); bad.pop("evidence")
            with self.assertRaises(direct.AdapterError): direct._validate(bad, 3)
        for status in ("accepted", "running"):
            frame = hello(); frame["active"] = {"seq": 1, "status": status, "code": "active", "evidence": "none"}
            direct._validate(frame, 3)
        frame = hello(); frame["receipts"] = [{"seq": 1, "status": "succeeded", "code": "done", "evidence": "client_simulated"}]
        direct._validate(frame, 3)
        with self.assertRaises(direct.AdapterError): direct._decode('{"v":3,"v":3}')
        with self.assertRaises(direct.AdapterError): direct._decode('"' + 'x' * direct.MAX_BYTES + '"')

    def test_progress_preserves_self_and_risk_blinks(self):
        adapter = direct.DirectAdapter(TOKEN, protocol_version=3)
        old = observation(); new = copy.deepcopy(old); new.update(seq=8, sampled_at_ms=101)
        new["motion"]["x"] = 1.7
        new["risk"].update(damage_epoch=3, monster_epoch=4)
        adapter._check_observation_progress(old, new)
        self.assertEqual(old["self"], new["self"], "damage blink must survive an unchanged/coalesced player sample")
        adapter._check_observation_progress(old, old, resync=True)
        changes = [lambda o: o.update(seq=7), lambda o: o.update(sampled_at_ms=99),
                   lambda o: o["risk"].update(damage_epoch=1), lambda o: o["risk"].update(monster_epoch=2),
                   lambda o: o["motion"].update(epoch=0), lambda o: o["self"].update(event_seq=2),
                   lambda o: o["self"]["health"].update(health=19),
                   lambda o: o["self"].update(connection_id="00000000-0000-4000-8000-000000000002")]
        for change in changes:
            bad = copy.deepcopy(new); change(bad)
            with self.assertRaises(direct.AdapterError): adapter._check_observation_progress(old, bad)
        bad = copy.deepcopy(old); bad["risk"]["near_monster"] = True
        with self.assertRaises(direct.AdapterError): adapter._check_observation_progress(old, bad, resync=True)
        changed = copy.deepcopy(new); changed["self"].update(seq=3, event_seq=4, observed_at_ms=51)
        changed["self"]["health"]["health"] = 19
        adapter._check_observation_progress(old, changed)
        next_world = copy.deepcopy(new)
        next_world["self"].update(seq=3, generation=2, event_seq=4, observed_at_ms=51,
                                  available=False, dimension=None, position=None, health=None)
        next_world.update(motion=None, enabled=False); next_world["risk"].update(damage_epoch=0, monster_epoch=0)
        adapter._check_observation_progress(old, next_world, resync=True)


class ControlAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = direct.DirectAdapter(TOKEN, protocol_version=3)
        self.url = await self.adapter.start()
        self.http = aiohttp.ClientSession()
        self.peers = []

    async def asyncTearDown(self):
        for peer in self.peers: await peer.close()
        await self.http.close()
        await self.adapter.close()

    async def connect(self, frame=None, protocols=("evelyn.minecraft.v3",)):
        peer = await self.http.ws_connect(self.url, headers={"Authorization": "Bearer " + TOKEN}, protocols=protocols)
        self.peers.append(peer)
        await peer.send_json(frame or hello())
        return peer

    async def ready(self):
        peer = await self.connect()
        self.assertEqual(await peer.receive_json(), {"v": 3, "type": "welcome", "session": "s1", "world": WORLD})
        await self.adapter.wait_connected(1)
        return peer

    async def receive_kind(self, peer, kind):
        for _ in range(5):
            value = await peer.receive_json(timeout=2)
            if value["type"] == kind: return value
        self.fail("expected frame missing")

    async def assert_closed(self, peer):
        for _ in range(3):
            message = await peer.receive(timeout=2)
            if message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED}: break
        else: self.fail("invalid peer remained connected")
        await asyncio.sleep(0)
        self.assertIsNone(self.adapter.snapshot()["observation"])

    async def test_flat_typed_commands_and_owned_snapshots(self):
        peer = await self.ready()
        cases = [(lambda: self.adapter.navigate(-3, 64, 4), {"action": "navigate", "target_x": -3, "target_y": 64, "target_z": 4}),
                 (lambda: self.adapter.look(180., -90.), {"action": "look", "yaw": 180., "pitch": -90.})]
        for operation, fields in cases:
            task = asyncio.create_task(operation())
            frame = await self.receive_kind(peer, "command")
            self.assertEqual(set(frame), {"v", "type", "session", "world", "seq", "request_id", "observation_seq", "max_age_ms", "run_ms"} | set(fields))
            self.assertEqual({key: frame[key] for key in fields}, fields)
            self.assertEqual(frame["observation_seq"], 7)
            await peer.send_json(receipt(frame)); result = await task
            self.assertEqual(result["evidence"], "client_simulated")
        view = self.adapter.snapshot(); view["observation"]["motion"]["x"] = 999
        self.assertEqual(self.adapter.snapshot()["observation"]["motion"]["x"], 1.6)
        self.assertGreaterEqual(view["received_age_ms"], 0)

    async def test_cached_success_keeps_matching_evidence_on_old_reply(self):
        peer = await self.ready()
        command = asyncio.create_task(self.adapter.look(0, 0))
        frame = await self.receive_kind(peer, "command"); await peer.send_json(receipt(frame)); await command
        query = asyncio.create_task(self.adapter.query(frame["seq"], session="s1", world=WORLD))
        frame = await self.receive_kind(peer, "query"); await peer.send_json(receipt(frame, "accepted"))
        known = await query
        self.assertEqual((known["status"], known["evidence"]), ("succeeded", "client_simulated"))
        self.assertEqual(self.adapter.snapshot()["receipts"][0]["evidence"], "client_simulated")

    async def test_argument_bounds_and_no_arbitrary_payload(self):
        await self.ready()
        for action, args in [("navigate", None), ("navigate", {"target_x": True, "target_y": 64, "target_z": 0}),
                             ("navigate", {"target_x": 30000001, "target_y": 64, "target_z": 0}),
                             ("navigate", {"target_x": 0, "target_y": 2049, "target_z": 0}),
                             ("navigate", {"target_x": 0., "target_y": 64, "target_z": 0}),
                             ("look", {"yaw": 0, "pitch": 91}), ("look", {"yaw": 361, "pitch": 0}),
                             ("look", {"yaw": float("nan"), "pitch": 0}),
                             ("look", {"yaw": 0, "pitch": 0, "extra": 1}), ("probe", {})]:
            with self.assertRaises(direct.AdapterError): await self.adapter.command(action, args=args)
        self.assertEqual(self.adapter.pending_count, 0)

    async def test_v1_command_shape_and_v2_read_only_are_preserved(self):
        v1 = direct.DirectAdapter(TOKEN)
        v2 = direct.DirectAdapter(TOKEN, protocol_version=2)
        v1._state = {"session": "legacy", "world": "w1", "capabilities": ["probe"], "next_seq": 1,
                     "observation": {"seq": 1, "tick": 1, "state": "ready"}}
        v1._ready.set()
        async def capture(kind, seq, timeout, **fields):
            self.assertEqual((kind, seq), ("command", 1))
            return fields
        v1._rpc = capture
        try:
            self.assertEqual(await v1.command("probe"), {"action": "probe", "observation_seq": 1, "max_age_ms": 1000, "run_ms": 1000})
            with self.assertRaisesRegex(direct.AdapterError, "invalid_arguments"):
                await v1.command("probe", args={"extra": 1})
            for adapter, code in ((v1, "control_mode_required"), (v2, "read_only")):
                for operation in (lambda: adapter.navigate(1, 64, 1), lambda: adapter.look(0, 0), lambda: adapter.off()):
                    with self.assertRaisesRegex(direct.AdapterError, code): await operation()
            with self.assertRaisesRegex(direct.AdapterError, "read_only"):
                await v2.command("navigate", args={"target_x": 1, "target_y": 64, "target_z": 1})
        finally:
            await v1.close(); await v2.close()

    async def test_query_cancel_ownership_and_unknown_recovery(self):
        peer = await self.ready()
        task = asyncio.create_task(self.adapter.navigate(2, 64, -3, timeout=.03))
        command = await self.receive_kind(peer, "command")
        with self.assertRaisesRegex(direct.AdapterError, "outcome_unknown"): await task
        self.assertTrue(self.adapter.snapshot()["resync_required"])
        with self.assertRaisesRegex(direct.AdapterError, "resync_required"): await self.adapter.look(0, 0)
        for method in (self.adapter.query, self.adapter.cancel):
            with self.assertRaisesRegex(direct.AdapterError, "stale_binding"): await method(1, session="other", world=WORLD)
        query = asyncio.create_task(self.adapter.query(command["seq"], session="s1", world=WORLD))
        frame = await self.receive_kind(peer, "query"); await peer.send_json(receipt(frame, "succeeded")); await query
        self.assertFalse(self.adapter.snapshot()["resync_required"])
        cancel = asyncio.create_task(self.adapter.cancel(command["seq"], session="s1", world=WORLD))
        frame = await self.receive_kind(peer, "cancel"); await peer.send_json(receipt(frame, "cancelled")); result = await cancel
        self.assertEqual(result["evidence"], "none")

    async def test_receipt_evidence_validation_disconnects(self):
        peer = await self.ready()
        task = asyncio.create_task(self.adapter.look(0, 0))
        frame = await self.receive_kind(peer, "command")
        await peer.send_json(receipt(frame, "succeeded", evidence="none"))
        await self.assert_closed(peer)
        with self.assertRaises(direct.AdapterError): await task

    async def test_off_sends_binding_then_closes_without_replay(self):
        peer = await self.ready(); self.adapter._uncertain = True
        task = asyncio.create_task(self.adapter.off())
        frame = await self.receive_kind(peer, "off")
        self.assertEqual(set(frame), {"v", "type", "session", "world", "request_id"})
        self.assertEqual((frame["session"], frame["world"]), ("s1", WORLD))
        self.assertTrue(frame["request_id"])
        await peer.close(); await task
        self.assertTrue(self.adapter.snapshot()["resync_required"])
        self.assertIsNone(self.adapter.snapshot()["observation"])
        with self.assertRaisesRegex(direct.AdapterError, "already_started_or_closed"): await self.adapter.start()

    async def test_off_during_pending_command_clears_no_unknown_outcome(self):
        peer = await self.ready()
        pending = asyncio.create_task(self.adapter.navigate(2, 64, -3))
        await self.receive_kind(peer, "command")
        stop = asyncio.create_task(self.adapter.off())
        await self.receive_kind(peer, "off")
        with self.assertRaises(direct.AdapterError): await self.adapter.look(0, 0)
        await peer.close(); await stop
        with self.assertRaisesRegex(direct.AdapterError, "outcome_unknown"): await pending
        self.assertTrue(self.adapter.snapshot()["resync_required"])
        self.assertEqual(self.adapter.pending_count, 0)

    async def test_new_generation_resync_fences_old_commands(self):
        peer = await self.ready()
        frame = hello(); frame["world"] = CONNECTION + ":2"
        frame["observation"].update(seq=8, sampled_at_ms=101, motion=None, enabled=False)
        frame["observation"]["self"].update(seq=3, generation=2, event_seq=4, observed_at_ms=51,
                                             available=False, dimension=None, position=None, health=None)
        frame["observation"]["risk"].update(damage_epoch=0, monster_epoch=0)
        await peer.send_json(frame)
        self.assertEqual((await self.receive_kind(peer, "welcome"))["world"], CONNECTION + ":2")
        with self.assertRaisesRegex(direct.AdapterError, "stale_binding"):
            await self.adapter.cancel(1, session="s1", world=WORLD)
        await peer.send_json(hello())
        await self.assert_closed(peer)

    async def test_monotonic_risk_and_same_self_live_frames(self):
        peer = await self.ready()
        wrapper = observation(); wrapper.update(seq=8, sampled_at_ms=101); wrapper["risk"]["damage_epoch"] = 3
        await peer.send_json({"v": 3, "type": "observe", "session": "s1", "world": WORLD, "observation": wrapper})
        for _ in range(50):
            if self.adapter.snapshot()["observation"]["seq"] == 8: break
            await asyncio.sleep(.005)
        self.assertEqual(self.adapter.snapshot()["observation"]["risk"]["damage_epoch"], 3)
        wrapper = copy.deepcopy(wrapper); wrapper.update(seq=9, sampled_at_ms=102); wrapper["risk"]["damage_epoch"] = 2
        await peer.send_json({"v": 3, "type": "observe", "session": "s1", "world": WORLD, "observation": wrapper})
        await self.assert_closed(peer)

    async def test_missing_subprotocol_is_rejected(self):
        peer = await self.connect(protocols=())
        await self.assert_closed(peer)


if __name__ == "__main__":
    attempt_spec = importlib.util.spec_from_file_location("_evelyn_test_attempt_capture", ROOT / "tools/run_test_attempt.py")
    attempt_capture = importlib.util.module_from_spec(attempt_spec)
    attempt_spec.loader.exec_module(attempt_capture)
    attempt_capture.install_unittest_capture()
    unittest.main()
