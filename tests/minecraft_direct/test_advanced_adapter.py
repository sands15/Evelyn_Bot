"""Owned v4 loopback contract tests; no game server, account, or Evelyn runtime imports."""
import asyncio
import copy
import importlib.util
from pathlib import Path
import unittest

import aiohttp

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("advanced_direct", ROOT / "evelyn_core/runtime/evelyn_core/minecraft_direct_adapter.py")
direct = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(direct)
TOKEN = "advanced-owned-synthetic-" + "x" * 32
CONNECTION = "00000000-0000-4000-8000-000000000001"
WORLD = CONNECTION + ":1"


def target(entity_id=9, epoch=1):
    return {"entity_id": entity_id, "spawn_epoch": epoch, "type": "minecraft:zombie",
            "x": 2.5, "y": 64., "z": 1.5, "width": .6, "height": 1.95,
            "health": 20., "geometry_known": True, "dead": False}


def observation():
    player = {"seq": 2, "connection_id": CONNECTION, "generation": 1, "event_seq": 3,
              "observed_at_ms": 50, "source": "server_observed", "available": True,
              "dimension": "minecraft:overworld", "position": {
                  "x": 1.5, "y": 64., "z": 1.5, "yaw": 0., "pitch": 0., "source": "server_correction"},
              "health": {"health": 20., "food": 20, "saturation": 5.}, "world_ready": False,
              "coverage": {"chunks": "unknown", "entities": "unknown", "inventory": "unknown"}}
    return {"seq": 7, "sampled_at_ms": 100, "source": "local_control", "self": player,
            "motion": {"x": 1.5, "y": 64., "z": 1.5, "yaw": 0., "pitch": 0.,
                       "vx": 0., "vy": -.08, "vz": 0., "epoch": 1, "source": "client_simulated"},
            "planner": {"status": "idle", "code": "idle", "goal_seq": 0, "step_id": 0, "paused": False, "preemptions": 0},
            "risk": {"damage_epoch": 2, "monster_epoch": 3, "near_monster": False,
                     "coverage": "server_tracked_partial", "tracked_count": 1}, "enabled": False,
            "locomotion": {"maintenance": True, "medium": "air", "air_supply": 300},
            "combat": {"status": "idle", "code": "idle", "target_id": None, "target_epoch": None,
                       "attacks_sent": 0, "hand": "empty", "evidence": "none"}, "targets": [target()]}


def hello():
    return {"v": 4, "type": "hello", "session": "s1", "world": WORLD, "incarnation": "i1",
            "next_seq": 1, "observation": observation(), "capabilities": ["attack", "jump", "look", "navigate"],
            "active": None, "receipts": []}


def receipt(frame, status="succeeded", code="done", evidence="client_simulated"):
    return {"v": 4, "type": "receipt", "session": frame["session"], "world": frame["world"],
            "seq": frame["seq"], "request_id": frame["request_id"], "next_seq": frame["seq"] + 1,
            "status": status, "code": code, "evidence": evidence}


class AdvancedSchemaTests(unittest.TestCase):
    def test_explicit_v4_and_initial_unknown(self):
        adapter = direct.DirectAdapter(TOKEN, protocol_version=4)
        direct._validate(hello(), 4)
        initial = hello(); initial.update(observation=None, world="none"); direct._validate(initial, 4)
        unknown = hello(); o = unknown["observation"]
        o["self"].update(available=False, dimension=None, position=None, health=None)
        o.update(motion=None, enabled=False, targets=[]); o["locomotion"]["maintenance"] = False
        direct._validate(unknown, 4)
        self.assertIsNone(adapter.snapshot()["received_age_ms"])

    def test_strict_new_shapes_bounds_and_unknown(self):
        changes = [lambda f: f.update(capabilities=["jump", "attack", "look", "navigate"]),
                   lambda f: f["observation"].pop("combat"),
                   lambda f: f["observation"]["locomotion"].update(extra=0),
                   lambda f: f["observation"]["locomotion"].update(maintenance=1),
                   lambda f: f["observation"]["locomotion"].update(medium="lava"),
                   lambda f: f["observation"]["locomotion"].update(air_supply=301),
                   lambda f: f["observation"]["locomotion"].update(air_supply=True),
                   lambda f: f["observation"]["combat"].update(target_id=1),
                   lambda f: f["observation"]["combat"].update(target_epoch=1),
                   lambda f: f["observation"]["combat"].update(target_id=1, target_epoch=0),
                   lambda f: f["observation"]["combat"].update(attacks_sent=129),
                   lambda f: f["observation"]["combat"].update(hand="sword"),
                   lambda f: f["observation"]["combat"].update(evidence="client_simulated"),
                   lambda f: f["observation"]["combat"].update(code="bad\ncode"),
                   lambda f: f["observation"].update(targets=[target(i) for i in range(33)]),
                   lambda f: f["observation"].update(targets=[target(), target()]),
                   lambda f: f["observation"].update(targets=[target(9), target(8)]),
                   lambda f: f["observation"]["targets"][0].update(entity_id=True),
                   lambda f: f["observation"]["targets"][0].update(spawn_epoch=0),
                   lambda f: f["observation"]["targets"][0].update(type="not a resource"),
                   lambda f: f["observation"]["targets"][0].update(x=30000001),
                   lambda f: f["observation"]["targets"][0].update(y=-2049),
                   lambda f: f["observation"]["targets"][0].update(z=float("inf")),
                   lambda f: f["observation"]["targets"][0].update(width=0),
                   lambda f: f["observation"]["targets"][0].update(height=32.1),
                   lambda f: f["observation"]["targets"][0].update(health=-1),
                   lambda f: f["observation"]["targets"][0].update(health=1025),
                   lambda f: f["observation"]["targets"][0].update(geometry_known=1),
                   lambda f: f["observation"]["targets"][0].update(dead="false")]
        for i, change in enumerate(changes):
            frame=hello(); change(frame)
            with self.subTest(case=i), self.assertRaises(direct.AdapterError): direct._validate(frame,4)
        frame=hello(); frame["observation"]["self"].update(available=False, dimension=None, position=None, health=None)
        frame["observation"].update(motion=None, enabled=False)
        with self.assertRaises(direct.AdapterError): direct._validate(frame,4)
        frame["observation"]["targets"]=[]
        with self.assertRaises(direct.AdapterError): direct._validate(frame,4)
        frame["observation"]["locomotion"]["maintenance"]=False; direct._validate(frame,4)
        frame=hello(); frame["observation"]["targets"][0].update(health=None, geometry_known=False)
        frame["observation"]["locomotion"]["air_supply"]=None; direct._validate(frame,4)

    def test_target_spawn_identity_and_death_progress(self):
        adapter=direct.DirectAdapter(TOKEN,protocol_version=4); old=observation()
        new=copy.deepcopy(old);new.update(seq=8,sampled_at_ms=101)
        new["targets"][0].update(x=2.6,health=19.); adapter._check_observation_progress(old,new)
        for change in [lambda t:t.update(spawn_epoch=0),lambda t:t.update(type="minecraft:creeper"),
                       lambda t:t.update(width=.7),lambda t:t.update(height=1.7)]:
            bad=copy.deepcopy(new);change(bad["targets"][0])
            with self.assertRaises(direct.AdapterError):adapter._check_observation_progress(old,bad)
        dead=copy.deepcopy(old);dead["targets"][0].update(dead=True,health=0)
        with self.assertRaises(direct.AdapterError):adapter._check_observation_progress(dead,new)
        new["targets"][0].update(spawn_epoch=2,type="minecraft:creeper",width=.7,height=1.7)
        adapter._check_observation_progress(dead,new)
        adapter._check_observation_progress(old,old,resync=True)
        bad=copy.deepcopy(old);bad["locomotion"]["medium"]="water"
        with self.assertRaises(direct.AdapterError):adapter._check_observation_progress(old,bad,resync=True)

    def test_success_evidence_requires_canonical_observed_defeat(self):
        base={"session":"s1","world":WORLD,"request_id":"r1","seq":1}
        direct._validate(receipt(base),4)
        observed=receipt(base,code="target_defeated_observed",evidence="server_observed")
        direct._validate(observed,4)
        for change in [dict(code="sent_attack"),dict(evidence="client_simulated"),dict(status="running"),dict(evidence="none")]:
            bad=dict(observed,**change)
            with self.assertRaises(direct.AdapterError):direct._validate(bad,4)
        for status in direct.STATUSES-{"succeeded"}:
            direct._validate(receipt(base,status=status,evidence="none"),4)
        frame=hello();frame["receipts"]=[{k:observed[k] for k in ("seq","status","code","evidence")}]
        direct._validate(frame,4)
        frame=hello();frame["active"]={"seq":1,"status":"running","code":"attack_wait","evidence":"none"}
        direct._validate(frame,4)
        v3=dict(observed,v=3)
        with self.assertRaises(direct.AdapterError):direct._validate(v3,3)


class AdvancedAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter=direct.DirectAdapter(TOKEN,protocol_version=4)
        self.url=await self.adapter.start();self.http=aiohttp.ClientSession();self.peers=[]

    async def asyncTearDown(self):
        for peer in self.peers:await peer.close()
        await self.http.close();await self.adapter.close()

    async def ready(self, protocols=("evelyn.minecraft.v4",)):
        peer=await self.http.ws_connect(self.url,headers={"Authorization":"Bearer "+TOKEN},protocols=protocols)
        self.peers.append(peer);await peer.send_json(hello())
        if protocols==("evelyn.minecraft.v4",):
            self.assertEqual(await peer.receive_json(),{"v":4,"type":"welcome","session":"s1","world":WORLD})
            await self.adapter.wait_connected(1)
        return peer

    async def receive(self,peer,kind):
        for _ in range(5):
            frame=await peer.receive_json(timeout=2)
            if frame["type"]==kind:return frame
        self.fail("frame missing")

    async def assert_closed(self,peer):
        for _ in range(3):
            message=await peer.receive(timeout=2)
            if message.type in {aiohttp.WSMsgType.CLOSE,aiohttp.WSMsgType.CLOSED}:break
        else:self.fail("peer remained connected")
        await asyncio.sleep(0);self.assertIsNone(self.adapter.snapshot()["observation"])

    async def test_jump_attack_half_step_and_look_wire(self):
        peer=await self.ready()
        cases=[(lambda:self.adapter.jump(),{"action":"jump"}),
               (lambda:self.adapter.attack(9,1),{"action":"attack","target_id":9,"target_epoch":1}),
               (lambda:self.adapter.navigate(-2,64.5,3),{"action":"navigate","target_x":-2,"target_y":64.5,"target_z":3}),
               (lambda:self.adapter.look(180.,-90.),{"action":"look","yaw":180.,"pitch":-90.})]
        for call,fields in cases:
            task=asyncio.create_task(call());frame=await self.receive(peer,"command")
            self.assertEqual(set(frame),{"v","type","session","world","seq","request_id","observation_seq","max_age_ms","run_ms"}|set(fields))
            self.assertEqual({k:frame[k] for k in fields},fields);self.assertEqual(frame["observation_seq"],7)
            await peer.send_json(receipt(frame));self.assertEqual((await task)["evidence"],"client_simulated")
        snap=self.adapter.snapshot();snap["observation"]["targets"][0]["health"]=0
        self.assertEqual(self.adapter.snapshot()["observation"]["targets"][0]["health"],20.)

    async def test_arguments_and_version_scopes(self):
        await self.ready()
        bad=[("jump",{"extra":1}),("attack",None),("attack",{"target_id":True,"target_epoch":1}),
             ("attack",{"target_id":-1,"target_epoch":1}),("attack",{"target_id":0,"target_epoch":0}),
             ("attack",{"target_id":0,"target_epoch":direct.MAX_SEQ+1}),
             ("navigate",{"target_x":0,"target_y":64.25,"target_z":0}),
             ("navigate",{"target_x":0,"target_y":float("nan"),"target_z":0}),
             ("navigate",{"target_x":0,"target_y":True,"target_z":0}),
             ("navigate",{"target_x":0,"target_y":2048.5,"target_z":0}),
             ("navigate",{"target_x":0.,"target_y":64.5,"target_z":0}),
             ("look",{"yaw":361,"pitch":0})]
        for action,args in bad:
            with self.subTest(action=action,args=args),self.assertRaises(direct.AdapterError):await self.adapter.command(action,args=args)
        self.assertEqual(self.adapter.pending_count,0)
        for version in (1,2,3):
            old=direct.DirectAdapter(TOKEN,protocol_version=version)
            try:
                with self.assertRaises(direct.AdapterError):await old.jump()
                with self.assertRaises(direct.AdapterError):await old.attack(9,1)
            finally:await old.close()

    async def test_off_remains_one_shot_and_uncertain(self):
        peer=await self.ready();self.adapter._uncertain=True
        task=asyncio.create_task(self.adapter.off());frame=await self.receive(peer,"off")
        self.assertEqual(set(frame),{"v","type","session","world","request_id"})
        self.assertEqual(frame["world"],WORLD);await task;await self.assert_closed(peer)
        self.assertTrue(self.adapter.snapshot()["resync_required"])
        with self.assertRaises(direct.AdapterError):await self.adapter.jump()
        with self.assertRaises(direct.AdapterError):await self.adapter.start()

    async def test_malformed_target_closes_and_does_not_refresh(self):
        peer=await self.ready();value=observation();value["seq"]+=1;value["targets"][0]["height"]=float("inf")
        await peer.send_str(__import__('json').dumps({"v":4,"type":"observe","session":"s1","world":WORLD,"observation":value}))
        await self.assert_closed(peer)

    async def test_dead_target_cannot_revive_in_same_spawn(self):
        peer=await self.ready();value=observation();value["seq"]+=1;value["targets"][0]["dead"]=True
        await peer.send_json({"v":4,"type":"observe","session":"s1","world":WORLD,"observation":value})
        await asyncio.sleep(.01);value["seq"]+=1;value["targets"][0]["dead"]=False
        await peer.send_json({"v":4,"type":"observe","session":"s1","world":WORLD,"observation":value})
        await self.assert_closed(peer)

    async def test_v3_websocket_cannot_enter_v4(self):
        peer=await self.ready(("evelyn.minecraft.v3",));await self.assert_closed(peer)

    async def test_cached_defeat_keeps_observed_evidence(self):
        peer=await self.ready();task=asyncio.create_task(self.adapter.attack(9,1))
        frame=await self.receive(peer,"command")
        await peer.send_json(receipt(frame,code="target_defeated_observed",evidence="server_observed"));await task
        task=asyncio.create_task(self.adapter.query(1,session="s1",world=WORLD));frame=await self.receive(peer,"query")
        await peer.send_json(receipt(frame,status="accepted",code="accepted",evidence="none"));result=await task
        self.assertEqual((result["status"],result["code"],result["evidence"]),("succeeded","target_defeated_observed","server_observed"))
        self.assertEqual(self.adapter.snapshot()["receipts"][0]["evidence"],"server_observed")

    async def test_invalid_defeat_receipt_closes(self):
        peer=await self.ready();task=asyncio.create_task(self.adapter.attack(9,1));frame=await self.receive(peer,"command")
        await peer.send_json(receipt(frame,code="attack_sent",evidence="server_observed"));await self.assert_closed(peer)
        with self.assertRaises(direct.AdapterError):await task

    async def test_timeout_query_cancel_preserve_binding_and_uncertainty(self):
        peer=await self.ready();task=asyncio.create_task(self.adapter.jump(timeout=.03));await self.receive(peer,"command")
        with self.assertRaisesRegex(direct.AdapterError,"outcome_unknown"):await task
        with self.assertRaisesRegex(direct.AdapterError,"resync_required"):await self.adapter.attack(9,1)
        with self.assertRaisesRegex(direct.AdapterError,"stale_binding"):await self.adapter.cancel(1,session="s1",world="other")
        task=asyncio.create_task(self.adapter.query(1,session="s1",world=WORLD));frame=await self.receive(peer,"query")
        await peer.send_json(receipt(frame));await task;self.assertFalse(self.adapter.snapshot()["resync_required"])
        task=asyncio.create_task(self.adapter.cancel(1,session="s1",world=WORLD));frame=await self.receive(peer,"cancel")
        await peer.send_json(receipt(frame,status="cancelled",evidence="none"));self.assertEqual((await task)["status"],"cancelled")


if __name__ == "__main__":
    unittest.main()
