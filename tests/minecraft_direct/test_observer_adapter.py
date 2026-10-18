"""Strict v2 read-only observations; synthetic loopback, no account or game server."""
import asyncio
import copy
import importlib.util
import inspect
import json
from pathlib import Path
import unittest

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("direct", ROOT / "evelyn_core/runtime/evelyn_core/minecraft_direct_adapter.py")
direct = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(direct)
TOKEN = "observer-synthetic-" + "x" * 32
CONNECTION = "00000000-0000-4000-8000-000000000001"


def observation():
    return {"seq": 2, "connection_id": CONNECTION, "generation": 1, "event_seq": 3,
            "observed_at_ms": 50, "source": "server_observed", "available": True,
            "dimension": "minecraft:overworld",
            "position": {"x": 1.5, "y": 64.0, "z": -2.5, "yaw": 90.0, "pitch": 0.0, "source": "server_correction"},
            "health": {"health": 20.0, "food": 20, "saturation": 5.0}, "world_ready": False,
            "coverage": {"chunks": "unknown", "entities": "unknown", "inventory": "unknown"}}


def hello():
    return {"v": 2, "type": "hello", "session": "s1", "world": CONNECTION + ":1",
            "incarnation": "i1", "next_seq": 1, "observation": observation(),
            "capabilities": [], "active": None, "receipts": []}


class ObservationSchemaTests(unittest.TestCase):
    def test_terrain_progress_keeps_geometry_and_overlapping_cells_consistent(self):
        adapter = direct.DirectAdapter(TOKEN, protocol_version=2)
        old = observation()
        old['coverage']['chunks'] = 'partial'
        old['terrain'] = {'min_y': -64, 'height': 384, 'loaded_chunks': 1, 'event_seq': 2,
                          'observed_at_ms': 40, 'encoding': 'opaque_state_id',
                          'blocks': [{'x': 0, 'y': 64, 'z': 0, 'state_id': 0}]}
        new = copy.deepcopy(old); new.update(seq=3, event_seq=4, observed_at_ms=60)
        adapter._check_observation_progress(old, new)
        for mutate in [lambda o: o.pop('terrain'), lambda o: o.update(dimension='minecraft:the_nether'), lambda o: o['terrain'].update(min_y=-80),
                       lambda o: o['terrain'].update(height=400), lambda o: o['terrain'].update(event_seq=1),
                       lambda o: o['terrain'].update(observed_at_ms=39), lambda o: o['terrain'].update(loaded_chunks=0),
                       lambda o: o['terrain']['blocks'][0].update(state_id=1)]:
            bad = copy.deepcopy(new); mutate(bad)
            with self.assertRaises(direct.AdapterError): adapter._check_observation_progress(old, bad)
        moved = copy.deepcopy(new); moved['terrain']['blocks'][0]['x'] = 1
        adapter._check_observation_progress(old, moved)
        changed = copy.deepcopy(new); changed['terrain'].update(event_seq=4, observed_at_ms=60)
        changed['terrain']['blocks'][0]['state_id'] = None
        adapter._check_observation_progress(old, changed)
        later = copy.deepcopy(new); later.update(generation=2); later.pop('terrain')
        adapter._check_observation_progress(old, later)
        legacy = copy.deepcopy(old); legacy.pop('terrain'); legacy['coverage']['chunks'] = 'unknown'
        other = copy.deepcopy(new); other['dimension'] = 'minecraft:the_nether'
        with self.assertRaises(direct.AdapterError): adapter._check_observation_progress(legacy, other)
        other.pop('terrain'); other['coverage']['chunks'] = 'unknown'
        with self.assertRaises(direct.AdapterError): adapter._check_observation_progress(legacy, other)
        other.update(available=False, dimension=None, position=None, health=None)
        with self.assertRaises(direct.AdapterError): adapter._check_observation_progress(legacy, other)
        unknown = observation(); unknown.update(available=False, dimension=None, position=None, health=None)
        adapter._check_observation_progress(unknown, new)

    def test_terrain_extension_preserves_unknown_and_raw_zero(self):
        frame = hello()
        frame['observation']['coverage']['chunks'] = 'partial'
        frame['observation']['terrain'] = {
            'min_y': -64, 'height': 384, 'loaded_chunks': 1, 'event_seq': 2,
            'observed_at_ms': 40, 'encoding': 'opaque_state_id',
            'blocks': [{'x': -1, 'y': 64, 'z': 0, 'state_id': 0},
                       {'x': 0, 'y': 64, 'z': 0, 'state_id': None}]}
        try:
            direct._validate(frame, 2)
        except direct.AdapterError:
            self.fail('bounded terrain extension is missing')
        for key, value in [('min_y', -63), ('height', 0), ('loaded_chunks', 257),
                           ('event_seq', 4), ('observed_at_ms', 51), ('encoding', 'air'),
                           ('blocks', [{'x': 0, 'y': 0, 'z': 0, 'state_id': 0}] * 65)]:
            bad = copy.deepcopy(frame); bad['observation']['terrain'][key] = value
            with self.subTest(key=key), self.assertRaises(direct.AdapterError): direct._validate(bad, 2)
        for field, value in [('x', True), ('y', 2048), ('state_id', -1), ('state_id', 'air'), ('extra', 1)]:
            bad = copy.deepcopy(frame); bad['observation']['terrain']['blocks'][0][field] = value
            with self.subTest(field=field), self.assertRaises(direct.AdapterError): direct._validate(bad, 2)
        for mutate in [lambda o: o.update(available=False, dimension=None, position=None, health=None),
                       lambda o: o['coverage'].update(chunks='unknown'),
                       lambda o: o['terrain']['blocks'].append(dict(o['terrain']['blocks'][0])),
                       lambda o: o['terrain'].update(extra=True)]:
            bad = copy.deepcopy(frame); mutate(bad['observation'])
            with self.assertRaises(direct.AdapterError): direct._validate(bad, 2)

    def test_valid_partial_and_unavailable(self):
        self.assertIn('protocol_version', inspect.signature(direct.DirectAdapter).parameters,
                      'explicit read-only v2 mode is missing')
        direct._validate(hello(), 2)
        frame = hello()
        frame["observation"].update(available=False, dimension=None, position=None, health=None)
        direct._validate(frame, 2)

    def test_version_shape_and_claims_fail_closed(self):
        changes = [("v", 1), ("next_seq", 2), ("capabilities", ["walk"]), ("active", {}), ("receipts", [{}])]
        for key, value in changes:
            with self.subTest(key=key):
                frame = hello(); frame[key] = value
                with self.assertRaises(direct.AdapterError): direct._validate(frame, 2)
        for key, value in [("world_ready", True), ("available", 1), ("source", "client_simulated"),
                           ("event_seq", True), ("observed_at_ms", -1), ("seq", 0),
                           ("generation", 0), ("dimension", "bad dimension"), ("secret", "no")]:
            with self.subTest(key=key):
                frame = hello(); frame["observation"][key] = value
                with self.assertRaises(direct.AdapterError): direct._validate(frame, 2)

    def test_numbers_coverage_binding_and_unavailable(self):
        for path, value in [(('position', 'x'), float('inf')), (('position', 'y'), True),
                            (('position', 'x'), 10 ** 400),
                            (('position', 'pitch'), 91), (('health', 'health'), -1),
                            (('health', 'food'), 1.5), (('coverage', 'chunks'), 'air')]:
            frame = hello(); frame['observation'][path[0]][path[1]] = value
            with self.assertRaises(direct.AdapterError): direct._validate(frame, 2)
        for key, value in [('world', CONNECTION + ':2'), ('observation', None)]:
            frame = hello(); frame[key] = value
            if key == 'observation': frame['world'] = 'none'
            if key == 'observation': direct._validate(frame, 2)
            else:
                with self.assertRaises(direct.AdapterError): direct._validate(frame, 2)
        frame = hello(); frame['observation']['available'] = False
        with self.assertRaises(direct.AdapterError): direct._validate(frame, 2)


class ObserverAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = direct.DirectAdapter(TOKEN, protocol_version=2)
        self.url = await self.adapter.start()
        self.http = aiohttp.ClientSession()
        self.peers = []

    async def asyncTearDown(self):
        for peer in self.peers: await peer.close()
        await self.http.close()
        await self.adapter.close()

    async def connect(self):
        peer = await self.http.ws_connect(self.url, headers={'Authorization': 'Bearer ' + TOKEN}, protocols=('evelyn.minecraft.v2',))
        self.peers.append(peer)
        await peer.send_json(hello())
        self.assertEqual((await peer.receive_json())['v'], 2)
        await self.adapter.wait_connected(1)
        return peer

    async def closed(self, peer):
        for _ in range(3):
            message = await peer.receive(timeout=2)
            if message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED}: break
        else: self.fail('invalid v2 frame did not close')
        await asyncio.sleep(0)
        self.assertIsNone(self.adapter.snapshot()['observation'])

    async def test_read_only_and_owned_snapshot(self):
        await self.connect()
        state = self.adapter.snapshot()
        self.assertFalse(state['observation']['world_ready'])
        self.assertGreaterEqual(state['received_age_ms'], 0)
        state['observation']['position']['x'] = 999
        self.assertEqual(self.adapter.snapshot()['observation']['position']['x'], 1.5)
        for operation in [self.adapter.command('walk'), self.adapter.query(1, session='s1', world=CONNECTION + ':1'),
                          self.adapter.cancel(1, session='s1', world=CONNECTION + ':1')]:
            with self.assertRaisesRegex(direct.AdapterError, 'read_only'): await operation
        self.assertEqual(self.adapter.pending_count, 0)

    async def test_stale_event_rejected_and_disconnect_invalidates(self):
        peer = await self.connect()
        frame = {key: hello()[key] for key in ('v', 'session', 'world')}
        new = observation(); new.update(seq=3, event_seq=2)
        await peer.send_json({**frame, 'type': 'observe', 'observation': new})
        await self.closed(peer)

    async def test_generation_change_and_old_generation_hello_rejected(self):
        peer = await self.connect()
        frame = hello(); frame['world'] = CONNECTION + ':2'
        frame['observation'].update(seq=3, event_seq=4, generation=2, available=False, dimension=None, position=None, health=None)
        await peer.send_json(frame)
        self.assertEqual((await peer.receive_json())['world'], CONNECTION + ':2')
        self.assertIsNone(self.adapter.snapshot()['observation']['position'])
        await peer.send_json(hello())
        await self.closed(peer)

    async def test_v1_and_missing_subprotocol_not_accepted(self):
        peer = await self.http.ws_connect(self.url, headers={'Authorization': 'Bearer ' + TOKEN})
        self.peers.append(peer)
        await peer.send_json(hello())
        await self.closed(peer)


if __name__ == '__main__':
    unittest.main()
