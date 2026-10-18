"""Explicit v5 inventory contract; synthetic loopback, no game/account/runtime imports."""
import asyncio
import copy
import importlib.util
from pathlib import Path
import unittest

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('player_direct', ROOT / 'evelyn_core/runtime/evelyn_core/minecraft_direct_adapter.py')
direct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(direct)
fixture_spec = importlib.util.spec_from_file_location('player_v4_fixture', ROOT / 'tests/minecraft_direct/test_advanced_adapter.py')
fixture = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixture)
TOKEN = 'player-owned-synthetic-' + 'p' * 40


def empty_stack():
    return {'state': 'empty', 'item_id': None, 'count': 0, 'patch_known': True}


def inventory(*, known=True, revision=1, page_start=0):
    return {'revision': revision, 'known': known, 'container_id': 0 if known else None,
            'state_id': 3 if known else None, 'menu_type_id': None,
            'slot_count': 46 if known else 0, 'page_start': page_start,
            'slots': [dict(index=i, **empty_stack()) for i in range(page_start, min(page_start + 16, 46))] if known else [],
            'carried': empty_stack() if known else None, 'selected': 0, 'properties': [] if known else None, 'source': 'server_observed'}


def hello():
    value = fixture.hello()
    value['v'] = 5
    value['capabilities'] = ['attack', 'inventory_page', 'inventory_transfer', 'jump', 'look', 'navigate', 'swap_hands']
    value['observation']['inventory'] = inventory()
    return value


class PlayerSchemaTests(unittest.TestCase):
    def test_smithing_error_and_cartography_have_official_property_shapes(self):
        # Official SmithingMenu adds hasRecipeError; CartographyTableMenu adds no data slots.
        smithing = hello()
        smithing['observation']['inventory'].update(container_id=1, menu_type_id=21, slot_count=40, properties=[None])
        direct._validate(smithing, 5)
        smithing['observation']['inventory']['properties'] = [1]
        direct._validate(smithing, 5)
        cartography = hello()
        cartography['observation']['inventory'].update(container_id=2, menu_type_id=23, slot_count=39, properties=[])
        direct._validate(cartography, 5)
        for value, invalid in ((smithing, []), (cartography, [None])):
            value['observation']['inventory']['properties'] = invalid
            with self.assertRaises(direct.AdapterError):
                direct._validate(value, 5)

    def test_menu_properties_are_received_signed_values_or_unknown(self):
        frame = hello()
        frame['observation']['inventory']['properties'] = []
        direct._validate(frame, 5)
        furnace = copy.deepcopy(frame)
        furnace['observation']['inventory'].update(container_id=1, menu_type_id=14, slot_count=39,
                                                   properties=[-32768, 0, 32767, None])
        direct._validate(furnace, 5)
        adapter = direct.DirectAdapter(TOKEN, protocol_version=5)
        old = furnace['observation']
        new = copy.deepcopy(old)
        new['inventory']['properties'][3] = 0
        adapter._check_inventory_pages(None, old)
        with self.assertRaises(direct.AdapterError):
            adapter._check_inventory_pages(old, new)
        new['inventory']['revision'] += 1
        adapter._check_inventory_pages(old, new)
        for values in ([0], [0] * 11, [False, 0, 0, 0], [32768, 0, 0, 0], [-32769, 0, 0, 0], [1.0, 0, 0, 0], None):
            invalid = copy.deepcopy(furnace)
            invalid['observation']['inventory']['properties'] = values
            with self.subTest(values=values):
                with self.assertRaises(direct.AdapterError):
                    direct._validate(invalid, 5)
        unknown = copy.deepcopy(frame)
        unknown['observation']['inventory'] = inventory(known=False)
        unknown['observation']['inventory']['properties'] = None
        direct._validate(unknown, 5)
        unknown['observation']['inventory']['properties'] = []
        with self.assertRaises(direct.AdapterError):
            direct._validate(unknown, 5)

    def test_explicit_version_and_inventory_baseline(self):
        adapter = direct.DirectAdapter(TOKEN, protocol_version=5)
        self.assertFalse(adapter.snapshot()['connected'])
        direct._validate(hello(), 5)
        unknown = hello()
        unknown['observation']['inventory'] = inventory(known=False, revision=0)
        direct._validate(unknown, 5)
        last = hello()
        last['observation']['inventory'] = inventory(page_start=32)
        last['observation']['inventory']['slots'][4].update(state='item', item_id=1, count=64)
        direct._validate(last, 5)

    def test_corrupt_inventory_or_partial_baseline_is_rejected(self):
        mutations = [
            lambda v: v.update(revision=True), lambda v: v.update(revision=-1),
            lambda v: v.update(slot_count=4097), lambda v: v.update(page_start=1),
            lambda v: v.update(page_start=48), lambda v: v['slots'].pop(),
            lambda v: v['slots'][1].update(index=0),
            lambda v: v['slots'][0].update(item_id=1),
            lambda v: v['slots'][0].update(count=1),
            lambda v: v['slots'][0].update(state='item', item_id=2, count=0),
            lambda v: v['slots'][0].update(state='item', item_id=True, count=1),
            lambda v: v['slots'][0].update(state='item', item_id=0, count=1),
            lambda v: v['carried'].update(state='item', item_id=0, count=1),
            lambda v: v['slots'][0].update(state='item', item_id=1658, count=1),
            lambda v: v['slots'][0].update(state='item', item_id=2, count=100),
            lambda v: v['slots'][0].update(patch_known=False),
            lambda v: v.update(carried=None), lambda v: v.update(selected=9),
            lambda v: v.update(container_id=-1), lambda v: v.update(state_id=True),
            lambda v: v.update(state_id=32768),
            lambda v: v.update(menu_type_id=-1), lambda v: v.update(source='client_simulated'),
            lambda v: v.update(unrequested='payload'), lambda v: v.update(known=False),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(i=i):
                frame = hello()
                mutate(frame['observation']['inventory'])
                with self.assertRaises(direct.AdapterError):
                    direct._validate(frame, 5)
        v4 = fixture.hello()
        v4['observation']['inventory'] = inventory()
        with self.assertRaises(direct.AdapterError):
            direct._validate(v4, 4)

    def test_inventory_revision_fences_equal_revision_state_changes(self):
        adapter = direct.DirectAdapter(TOKEN, protocol_version=5)
        old = hello()['observation']
        for mutate in (lambda x: x.update(revision=0), lambda x: x.update(selected=1),
                       lambda x: x['slots'][0].update(state='item', item_id=3, count=1)):
            new = copy.deepcopy(old)
            new['seq'] += 1
            mutate(new['inventory'])
            with self.assertRaises(direct.AdapterError):
                adapter._check_observation_progress(old, new)
        new = copy.deepcopy(old)
        new['seq'] += 1
        new['inventory'] = inventory(page_start=16)
        adapter._check_observation_progress(old, new)
        new['inventory'] = inventory(known=False, revision=2)
        adapter._check_observation_progress(old, new)

    def test_inventory_page_roundtrip_keeps_bounded_same_revision_history(self):
        adapter = direct.DirectAdapter(TOKEN, protocol_version=5)
        first = hello()['observation']
        middle = copy.deepcopy(first)
        middle['seq'] += 1
        middle['inventory'] = inventory(page_start=16)
        adapter._check_observation_progress(first, middle)
        last = copy.deepcopy(first)
        last['seq'] += 2
        adapter._check_observation_progress(middle, last)
        changed = copy.deepcopy(last)
        changed['seq'] += 1
        changed['inventory']['slots'][0].update(state='item', item_id=3, count=1)
        again = copy.deepcopy(middle)
        again['seq'] = last['seq'] + 1
        adapter._check_observation_progress(last, again)
        changed['seq'] = again['seq'] + 1
        with self.assertRaises(direct.AdapterError):
            adapter._check_observation_progress(again, changed)
        adapter._check_observation_progress(again, again, resync=True)
        with self.assertRaises(direct.AdapterError):
            adapter._check_observation_progress(again, changed, resync=True)
        changed['inventory']['revision'] += 1
        adapter._check_observation_progress(again, changed)
        fresh = copy.deepcopy(first)
        fresh['seq'] = changed['seq'] + 1
        fresh['self']['generation'] += 1
        fresh['self']['seq'] += 1
        fresh['self']['event_seq'] += 1
        fresh['self']['observed_at_ms'] += 1
        adapter._check_observation_progress(changed, fresh)
        self.assertLessEqual(len(adapter._inventory_seen), 4096)

    def test_inventory_page_history_is_limited_to_current_revision(self):
        adapter = direct.DirectAdapter(TOKEN, protocol_version=5)
        old = None
        for page in range(0, 4096, 16):
            new = hello()['observation']
            new['seq'] = page + 1
            new['inventory'] = inventory(page_start=page)
            new['inventory']['slot_count'] = 4096
            new['inventory']['slots'] = [dict(index=i, **empty_stack()) for i in range(page, page + 16)]
            adapter._check_observation_progress(old, new)
            old = new
        self.assertEqual(len(adapter._inventory_seen), 4096)
        new = hello()['observation']
        new['seq'] = old['seq'] + 1
        new['inventory']['revision'] = 2
        adapter._check_observation_progress(old, new)
        self.assertEqual(len(adapter._inventory_seen), 16)


class PlayerAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_swap_requires_seen_hands_and_exact_revision(self):
        adapter = direct.DirectAdapter(TOKEN, protocol_version=5)
        adapter._state = hello()
        observed = adapter._state['observation']
        observed['inventory'] = inventory(page_start=32)
        observed['inventory']['slots'][4].update(state='item', item_id=1, count=8)
        observed['inventory']['slots'][13].update(state='item', item_id=2, count=3)
        adapter._check_inventory_pages(None, observed)
        adapter._ready.set()
        sent = []
        async def rpc(kind, seq, timeout, **fields):
            sent.append(fields)
            return fields
        adapter._rpc = rpc
        result = await adapter.swap_hands(expected_revision=1)
        self.assertEqual({key: result[key] for key in ('action', 'inventory_revision')},
                         {'action': 'swap_hands', 'inventory_revision': 1})
        for revision in (0, -1, True, 1.0, None):
            with self.subTest(revision=revision):
                with self.assertRaises(direct.AdapterError):
                    await adapter.swap_hands(expected_revision=revision)
        original = copy.deepcopy(observed['inventory'])
        for field, value in (('known', False), ('container_id', 1), ('selected', None), ('menu_type_id', 0)):
            observed['inventory'] = dict(copy.deepcopy(original), **{field: value})
            with self.subTest(field=field):
                with self.assertRaises(direct.AdapterError):
                    await adapter.swap_hands(expected_revision=1)
        observed['inventory'] = original
        adapter._inventory_seen.pop(45)
        with self.assertRaises(direct.AdapterError):
            await adapter.swap_hands(expected_revision=1)
        self.assertEqual(len(sent), 1)
        direct._receipt({'seq': 1, 'status': 'succeeded', 'code': 'hand_swap_observed', 'evidence': 'server_observed'}, 5)
        with self.assertRaises(direct.AdapterError):
            direct._receipt({'seq': 1, 'status': 'succeeded', 'code': 'hand_swap_observed', 'evidence': 'client_simulated'}, 5)

    async def test_transfer_arguments_require_seen_source_destination_and_revision(self):
        adapter = direct.DirectAdapter(TOKEN, protocol_version=5)
        adapter._state = hello()
        adapter._state['capabilities'] = ['attack', 'inventory_page', 'inventory_transfer', 'jump', 'look', 'navigate', 'swap_hands']
        observation = adapter._state['observation']
        observation['inventory'] = inventory(page_start=32)
        observation['inventory']['slots'][4].update(state='item', item_id=1, count=8)
        adapter._check_inventory_pages(None, observation)
        adapter._ready.set()
        sent = []
        async def rpc(kind, seq, timeout, **fields):
            sent.append((kind, seq, fields))
            return fields
        adapter._rpc = rpc
        result = await adapter.inventory_transfer(36, 37, 3, expected_revision=1)
        self.assertEqual({k: result[k] for k in ('action', 'source_slot', 'destination_slot', 'amount', 'inventory_revision')},
                         {'action': 'inventory_transfer', 'source_slot': 36, 'destination_slot': 37, 'amount': 3, 'inventory_revision': 1})
        for source, destination, amount, revision in ((36, 36, 1, 1), (36, 37, 9, 1), (36, 37, 0, 1),
                                                     (True, 37, 1, 1), (36, 5, 1, 1), (36, 37, 1, 0), (36, 20, 1, 1)):
            with self.subTest(source=source, destination=destination, amount=amount, revision=revision):
                with self.assertRaises(direct.AdapterError):
                    await adapter.inventory_transfer(source, destination, amount, expected_revision=revision)
        self.assertEqual(len(sent), 1)
        direct._receipt({'seq': 1, 'status': 'succeeded', 'code': 'inventory_transfer_observed', 'evidence': 'server_observed'}, 5)
        with self.assertRaises(direct.AdapterError):
            direct._receipt({'seq': 1, 'status': 'succeeded', 'code': 'inventory_transfer_observed', 'evidence': 'client_simulated'}, 5)

    async def test_page_roundtrip_mutation_disconnects_and_new_session_starts_clean(self):
        adapter = direct.DirectAdapter(TOKEN, protocol_version=5)
        http = aiohttp.ClientSession()
        peer = None
        try:
            url = await adapter.start()
            peer = await http.ws_connect(url, protocols=['evelyn.minecraft.v5'], headers={'Authorization': 'Bearer ' + TOKEN})
            await peer.send_json(hello())
            await peer.receive_json(timeout=2)
            await adapter.wait_connected()
            for seq, page in ((8, 16), (9, 0), (10, 16)):
                observation = hello()['observation']
                observation['seq'] = seq
                observation['inventory'] = inventory(page_start=page)
                await peer.send_json({'v': 5, 'type': 'observe', 'session': 's1', 'world': fixture.WORLD, 'observation': observation})
                for _ in range(50):
                    if (adapter.snapshot().get('observation') or {}).get('seq') == seq:
                        break
                    await asyncio.sleep(.01)
                self.assertEqual(adapter.snapshot()['observation']['seq'], seq)
            observation = hello()['observation']
            observation['seq'] = 11
            observation['inventory']['slots'][0].update(state='item', item_id=3, count=1)
            await peer.send_json({'v': 5, 'type': 'observe', 'session': 's1', 'world': fixture.WORLD, 'observation': observation})
            self.assertEqual((await peer.receive(timeout=2)).type, aiohttp.WSMsgType.CLOSE)
            for _ in range(50):
                if adapter._ws is None:
                    break
                await asyncio.sleep(.01)
            self.assertFalse(adapter.snapshot()['connected'])
            self.assertEqual(adapter._inventory_seen, {})
            peer = await http.ws_connect(url, protocols=['evelyn.minecraft.v5'], headers={'Authorization': 'Bearer ' + TOKEN})
            fresh = hello()
            fresh['session'] = 's2'
            fresh['observation'] = observation
            await peer.send_json(fresh)
            self.assertEqual((await peer.receive_json(timeout=2))['session'], 's2')
            await adapter.wait_connected()
            self.assertEqual(adapter.snapshot()['observation']['inventory']['slots'][0]['item_id'], 3)
        finally:
            if peer is not None:
                await peer.close()
            await http.close()
            await adapter.close()
        self.assertEqual(adapter._inventory_seen, {})

    async def test_page_request_is_bound_to_observed_revision_and_disconnect(self):
        adapter = direct.DirectAdapter(TOKEN, protocol_version=5)
        http = aiohttp.ClientSession()
        try:
            url = await adapter.start()
            peer = await http.ws_connect(url, protocols=['evelyn.minecraft.v5'], headers={'Authorization': 'Bearer ' + TOKEN})
            await peer.send_json(hello())
            welcome = await peer.receive_json(timeout=2)
            self.assertEqual(welcome, {'v': 5, 'type': 'welcome', 'session': 's1', 'world': fixture.WORLD})
            await adapter.wait_connected()
            for args in ({'page_start': 48, 'inventory_revision': 1}, {'page_start': 16, 'inventory_revision': 0},
                         {'page_start': True, 'inventory_revision': 1}):
                with self.assertRaises(direct.AdapterError):
                    await adapter.command('inventory_page', args=args)
            self.assertEqual(adapter.snapshot()['next_seq'], 1)
            pending = asyncio.create_task(adapter.inventory_page(16, expected_revision=1))
            request = await peer.receive_json(timeout=2)
            self.assertEqual({k: request[k] for k in ('v', 'type', 'action', 'page_start', 'inventory_revision', 'seq')},
                             {'v': 5, 'type': 'command', 'action': 'inventory_page', 'page_start': 16, 'inventory_revision': 1, 'seq': 1})
            response = fixture.receipt(request, code='inventory_page_selected')
            response['v'] = 5
            await peer.send_json(response)
            self.assertEqual((await pending)['status'], 'succeeded')
            await peer.close()
            for _ in range(50):
                if not adapter.snapshot()['connected']:
                    break
                await asyncio.sleep(.01)
            self.assertIsNone(adapter.snapshot()['observation'])
            self.assertEqual(adapter.pending_count, 0)
        finally:
            await http.close()
            await adapter.close()


if __name__ == '__main__':
    attempt_spec = importlib.util.spec_from_file_location("_evelyn_test_attempt_capture", ROOT / "tools/run_test_attempt.py")
    attempt_capture = importlib.util.module_from_spec(attempt_spec)
    attempt_spec.loader.exec_module(attempt_capture)
    attempt_capture.install_unittest_capture()
    unittest.main()
