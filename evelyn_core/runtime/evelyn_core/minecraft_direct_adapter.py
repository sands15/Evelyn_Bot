"""Explicit, loopback-only bridge for the independent Java communication peer.

Importing this module does not start a service or import the Evelyn runtime.
Game execution and authorization decisions belong to the explicit host caller.
"""
import asyncio
import copy
import hmac
import json
import math
import re
import time
import uuid

from aiohttp import WSMsgType, web

MAX_BYTES = 32768
MAX_SEQ = 2147483647
COMMON = {"v", "type", "session", "world"}
STATUSES = {"accepted", "running", "succeeded", "failed", "cancelled", "unknown", "rejected"}
TERMINAL = STATUSES - {"accepted", "running"}


class AdapterError(RuntimeError):
    """Fixed, content-free error code. An unknown outcome must not be retried."""


def _shape(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise AdapterError("invalid_message")


def _symbol(value):
    if type(value) is not str or not 1 <= len(value) <= 128 or not all(32 <= ord(c) <= 126 for c in value):
        raise AdapterError("invalid_message")
    return value


def _integer(value, maximum=MAX_SEQ):
    if type(value) is not int or not 1 <= value <= maximum:
        raise AdapterError("invalid_message")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AdapterError("duplicate_key")
        result[key] = value
    return result


def _constant(_):
    raise AdapterError("invalid_message")


def _decode(text):
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise AdapterError("message_too_large")
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, RecursionError):
        raise AdapterError("invalid_message") from None


def _observation(value):
    _shape(value, {"seq", "tick", "state"})
    _integer(value["seq"])
    _integer(value["tick"])
    _symbol(value["state"])


def _receipt(value, protocol_version=1):
    _shape(value, {"seq", "status", "code"} | ({"evidence"} if protocol_version in (3, 4, 5) else set()))
    _integer(value["seq"])
    if value["status"] not in STATUSES:
        raise AdapterError("invalid_message")
    _symbol(value["code"])
    if protocol_version == 3 and value["evidence"] != ("client_simulated" if value["status"] == "succeeded" else "none"):
        raise AdapterError("invalid_message")
    if protocol_version in (4, 5):
        if value["status"] == "succeeded":
            observed = value["code"] == "target_defeated_observed" or protocol_version == 5 and value["code"] in ("inventory_transfer_observed", "hand_swap_observed")
            expected = "server_observed" if observed else "client_simulated"
            if value["evidence"] != expected:
                raise AdapterError("invalid_message")
        elif value["evidence"] != "none":
            raise AdapterError("invalid_message")


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _player_observation(value):
    fields = {"seq", "connection_id", "generation", "event_seq", "observed_at_ms", "source",
              "available", "dimension", "position", "health", "world_ready", "coverage"}
    terrain_present = type(value) is dict and 'terrain' in value
    _shape(value, fields | ({'terrain'} if terrain_present else set()))
    _integer(value["seq"])
    _integer(value["generation"])
    connection = value["connection_id"]
    if type(connection) is not str or not re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", connection):
        raise AdapterError("invalid_message")
    for key, maximum in (("event_seq", MAX_SEQ), ("observed_at_ms", 9007199254740991)):
        if type(value[key]) is not int or not 0 <= value[key] <= maximum:
            raise AdapterError("invalid_message")
    if (value["source"] != "server_observed" or value["world_ready"] is not False or
            type(value["available"]) is not bool or
            value["coverage"] != {"chunks": "partial" if terrain_present else "unknown", "entities": "unknown", "inventory": "unknown"}):
        raise AdapterError("invalid_message")
    if not value["available"]:
        if terrain_present or any(value[key] is not None for key in ("dimension", "position", "health")):
            raise AdapterError("invalid_message")
        return
    dimension = value["dimension"]
    if type(dimension) is not str or len(dimension) > 128 or not re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9/._-]+", dimension):
        raise AdapterError("invalid_message")
    position = value["position"]
    if position is not None:
        _shape(position, {"x", "y", "z", "yaw", "pitch", "source"})
        if position["source"] != "server_correction":
            raise AdapterError("invalid_message")
        for key in ("x", "y", "z", "yaw", "pitch"):
            if not _finite_number(position[key]):
                raise AdapterError("invalid_message")
        if not -90 <= position["pitch"] <= 90:
            raise AdapterError("invalid_message")
    health = value["health"]
    if health is not None:
        _shape(health, {"health", "food", "saturation"})
        for key in ("health", "saturation"):
            if not _finite_number(health[key]) or health[key] < 0:
                raise AdapterError("invalid_message")
        if type(health["food"]) is not int or not 0 <= health["food"] <= MAX_SEQ:
            raise AdapterError("invalid_message")
    if terrain_present:
        _terrain(value['terrain'], value)


def _terrain(value, observation):
    _shape(value, {'min_y', 'height', 'loaded_chunks', 'event_seq', 'observed_at_ms', 'encoding', 'blocks'})
    for key, lower, upper in [('min_y', -2032, 2031), ('height', 16, 4064), ('loaded_chunks', 0, 256),
                              ('event_seq', 0, observation['event_seq']),
                              ('observed_at_ms', 0, observation['observed_at_ms'])]:
        if type(value[key]) is not int or not lower <= value[key] <= upper:
            raise AdapterError('invalid_message')
    if (value['min_y'] % 16 or value['height'] % 16 or value['min_y'] + value['height'] > 2032 or
            value['encoding'] != 'opaque_state_id' or type(value['blocks']) is not list or len(value['blocks']) > 64):
        raise AdapterError('invalid_message')
    seen = set()
    for block in value['blocks']:
        _shape(block, {'x', 'y', 'z', 'state_id'})
        for key, lower, upper in [('x', -33554432, 33554431), ('y', -2048, 2047), ('z', -33554432, 33554431)]:
            if type(block[key]) is not int or not lower <= block[key] <= upper:
                raise AdapterError('invalid_message')
        point = (block['x'], block['y'], block['z'])
        if point in seen:
            raise AdapterError('invalid_message')
        seen.add(point)
        state = block['state_id']
        if state is not None:
            if (type(state) is not int or not 0 <= state <= MAX_SEQ or value['loaded_chunks'] == 0 or
                    not value['min_y'] <= block['y'] < value['min_y'] + value['height']):
                raise AdapterError('invalid_message')


def _nonnegative(value, maximum=MAX_SEQ):
    if type(value) is not int or not 0 <= value <= maximum:
        raise AdapterError("invalid_message")


def _control_observation(value, advanced=False):
    _shape(value, {"seq", "sampled_at_ms", "source", "self", "motion", "planner", "risk", "enabled"}
           | ({"locomotion", "combat", "targets"} if advanced else set()))
    _integer(value["seq"])
    _nonnegative(value["sampled_at_ms"], 9007199254740991)
    if value["source"] != "local_control" or type(value["enabled"]) is not bool:
        raise AdapterError("invalid_message")
    _player_observation(value["self"])
    motion = value["motion"]
    if motion is not None:
        _shape(motion, {"x", "y", "z", "yaw", "pitch", "vx", "vy", "vz", "epoch", "source"})
        if motion["source"] != "client_simulated":
            raise AdapterError("invalid_message")
        for key in ("x", "y", "z", "yaw", "pitch", "vx", "vy", "vz"):
            if not _finite_number(motion[key]):
                raise AdapterError("invalid_message")
        if not -90 <= motion["pitch"] <= 90:
            raise AdapterError("invalid_message")
        _nonnegative(motion["epoch"])
    if not value["self"]["available"] or value["self"]["position"] is None:
        if motion is not None or value["enabled"]:
            raise AdapterError("invalid_message")
    planner = value["planner"]
    _shape(planner, {"status", "code", "goal_seq", "step_id", "paused", "preemptions"})
    _symbol(planner["status"])
    _symbol(planner["code"])
    for key in ("goal_seq", "step_id", "preemptions"):
        _nonnegative(planner[key])
    if type(planner["paused"]) is not bool:
        raise AdapterError("invalid_message")
    risk = value["risk"]
    _shape(risk, {"damage_epoch", "monster_epoch", "near_monster", "coverage", "tracked_count"})
    for key in ("damage_epoch", "monster_epoch"):
        _nonnegative(risk[key])
    _nonnegative(risk["tracked_count"], 1024)
    if type(risk["near_monster"]) is not bool or risk["coverage"] != "server_tracked_partial":
        raise AdapterError("invalid_message")


def _advanced_observation(value):
    _control_observation(value, advanced=True)
    locomotion = value["locomotion"]
    _shape(locomotion, {"maintenance", "medium", "air_supply"})
    if type(locomotion["maintenance"]) is not bool or locomotion["medium"] not in ("unknown", "ground", "air", "water"):
        raise AdapterError("invalid_message")
    if locomotion["air_supply"] is not None:
        _nonnegative(locomotion["air_supply"], 300)
    combat = value["combat"]
    _shape(combat, {"status", "code", "target_id", "target_epoch", "attacks_sent", "hand", "evidence"})
    _symbol(combat["status"])
    _symbol(combat["code"])
    if (combat["target_id"] is None) != (combat["target_epoch"] is None):
        raise AdapterError("invalid_message")
    if combat["target_id"] is not None:
        _nonnegative(combat["target_id"])
        _integer(combat["target_epoch"])
    _nonnegative(combat["attacks_sent"], 128)
    if combat["hand"] not in ("unknown", "empty", "occupied") or combat["evidence"] not in ("none", "server_observed"):
        raise AdapterError("invalid_message")
    targets = value["targets"]
    if type(targets) is not list or len(targets) > 32:
        raise AdapterError("invalid_message")
    previous_id = -1
    for target in targets:
        _shape(target, {"entity_id", "spawn_epoch", "type", "x", "y", "z", "width", "height", "health", "geometry_known", "dead"})
        _nonnegative(target["entity_id"])
        _integer(target["spawn_epoch"])
        if target["entity_id"] <= previous_id:
            raise AdapterError("invalid_message")
        previous_id = target["entity_id"]
        name = target["type"]
        if type(name) is not str or len(name) > 128 or not re.fullmatch(r"[a-z0-9_.-]+:[a-z0-9/._-]+", name):
            raise AdapterError("invalid_message")
        for key, bound in (("x", 30000000), ("y", 2048), ("z", 30000000)):
            if not _finite_number(target[key]) or not -bound <= target[key] <= bound:
                raise AdapterError("invalid_message")
        for key in ("width", "height"):
            if not _finite_number(target[key]) or not 0 < target[key] <= 32:
                raise AdapterError("invalid_message")
        if target["health"] is not None and (not _finite_number(target["health"]) or not 0 <= target["health"] <= 1024):
            raise AdapterError("invalid_message")
        if type(target["geometry_known"]) is not bool or type(target["dead"]) is not bool:
            raise AdapterError("invalid_message")
    if not value["self"]["available"] and (locomotion["maintenance"] or targets):
        raise AdapterError("invalid_message")


def _inventory_stack(value):
    _shape(value, {"state", "item_id", "count", "patch_known"})
    _nonnegative(value["count"], 99)
    if value["patch_known"] is not True:
        raise AdapterError("invalid_message")
    if value["state"] == "empty":
        if value["item_id"] is not None or value["count"] != 0:
            raise AdapterError("invalid_message")
    elif value["state"] == "item":
        _nonnegative(value["item_id"], 1657)
        if value["item_id"] == 0 or value["count"] == 0:
            raise AdapterError("invalid_message")
    else:
        raise AdapterError("invalid_message")


def _inventory_observation(value):
    _shape(value, {"revision", "known", "container_id", "state_id", "menu_type_id", "slot_count",
                   "page_start", "slots", "carried", "selected", "properties", "source"})
    _nonnegative(value["revision"])
    _nonnegative(value["slot_count"], 4096)
    _nonnegative(value["page_start"], 4095)
    if type(value["known"]) is not bool or value["source"] != "server_observed":
        raise AdapterError("invalid_message")
    if value["selected"] is not None:
        _nonnegative(value["selected"], 8)
    if type(value["slots"]) is not list or len(value["slots"]) > 16:
        raise AdapterError("invalid_message")
    if not value["known"]:
        if any(value[k] is not None for k in ("container_id", "state_id", "menu_type_id", "carried", "properties")) or value["slot_count"] != 0 or value["page_start"] != 0 or value["slots"]:
            raise AdapterError("invalid_message")
        return
    _nonnegative(value["container_id"])
    _nonnegative(value["state_id"], 32767)
    if value["menu_type_id"] is not None:
        _nonnegative(value["menu_type_id"], 24)
    property_counts = (0, 0, 0, 0, 0, 0, 0, 10, 1, 3, 4, 4, 0, 10, 4, 0, 0, 1, 1, 0, 0, 1, 4, 0, 1)
    expected = 0 if value["menu_type_id"] is None else property_counts[value["menu_type_id"]]
    if type(value["properties"]) is not list or len(value["properties"]) != expected:
        raise AdapterError("invalid_message")
    for property_value in value["properties"]:
        if property_value is not None and (type(property_value) is not int or not -32768 <= property_value <= 32767):
            raise AdapterError("invalid_message")
    start, size = value["page_start"], value["slot_count"]
    if start % 16 or start >= size or len(value["slots"]) != min(16, size - start):
        raise AdapterError("invalid_message")
    for index, slot in enumerate(value["slots"], start):
        _shape(slot, {"index", "state", "item_id", "count", "patch_known"})
        if type(slot["index"]) is not int or slot["index"] != index:
            raise AdapterError("invalid_message")
        _inventory_stack({k: v for k, v in slot.items() if k != "index"})
    _inventory_stack(value["carried"])


def _player_control_observation(value):
    if type(value) is not dict or "inventory" not in value:
        raise AdapterError("invalid_message")
    _advanced_observation({k: v for k, v in value.items() if k != "inventory"})
    _inventory_observation(value["inventory"])


def _version_observation(value, protocol_version):
    {1: _observation, 2: _player_observation, 3: _control_observation, 4: _advanced_observation,
     5: _player_control_observation}[protocol_version](value)


def _validate(frame, protocol_version=1):
    if type(frame) is not dict or type(frame.get("v")) is not int or frame["v"] != protocol_version:
        raise AdapterError("invalid_message")
    _symbol(frame.get("session"))
    _symbol(frame.get("world"))
    kind = frame.get("type")
    if kind == "hello":
        _shape(frame, COMMON | {"incarnation", "next_seq", "observation", "capabilities", "active", "receipts"})
        _symbol(frame["incarnation"])
        _integer(frame["next_seq"])
        if frame["observation"] is not None:
            _version_observation(frame["observation"], protocol_version)
        caps = frame["capabilities"]
        if type(caps) is not list or len(caps) > 16:
            raise AdapterError("invalid_message")
        for capability in caps:
            _symbol(capability)
        if len(set(caps)) != len(caps):
            raise AdapterError("invalid_message")
        if type(frame["receipts"]) is not list or len(frame["receipts"]) > 64:
            raise AdapterError("invalid_message")
        for receipt in frame["receipts"]:
            _receipt(receipt, protocol_version)
        if frame["active"] is not None:
            _receipt(frame["active"], protocol_version)
            if frame["active"]["status"] not in {"accepted", "running"}:
                raise AdapterError("invalid_message")
    elif kind == "observe":
        _shape(frame, COMMON | {"observation"})
        _version_observation(frame["observation"], protocol_version)
    elif kind == "receipt":
        receipt_keys = ("seq", "status", "code", "evidence") if protocol_version in (3, 4, 5) else ("seq", "status", "code")
        _shape(frame, COMMON | {"request_id", "next_seq"} | set(receipt_keys))
        _receipt({key: frame[key] for key in receipt_keys}, protocol_version)
        _integer(frame["next_seq"])
        if frame["request_id"] is not None:
            _symbol(frame["request_id"])
    elif kind == "heartbeat":
        _shape(frame, COMMON)
    else:
        raise AdapterError("invalid_message")
    if protocol_version == 2:
        if kind not in {"hello", "observe", "heartbeat"}:
            raise AdapterError("invalid_message")
        if kind == "hello" and (frame["next_seq"] != 1 or frame["capabilities"] or frame["active"] is not None or frame["receipts"]):
            raise AdapterError("invalid_message")
    if protocol_version == 3 and kind == "hello" and frame["capabilities"] != ["look", "navigate"]:
        raise AdapterError("invalid_message")
    if protocol_version == 4 and kind == "hello" and frame["capabilities"] != ["attack", "jump", "look", "navigate"]:
        raise AdapterError("invalid_message")
    if protocol_version == 5 and kind == "hello" and frame["capabilities"] != ["attack", "inventory_page", "inventory_transfer", "jump", "look", "navigate", "swap_hands"]:
        raise AdapterError("invalid_message")
    if protocol_version in (2, 3, 4, 5) and kind in {"hello", "observe"}:
        observation = frame["observation"]
        if observation is not None and protocol_version in (3, 4, 5):
            observation = observation["self"]
        binding = "none" if observation is None else f'{observation["connection_id"]}:{observation["generation"]}'
        if frame["world"] != binding:
            raise AdapterError("invalid_message")


class DirectAdapter:
    """One explicitly started local peer. No game launch, replay or reconnect."""

    def __init__(self, token, *, protocol_version=1):
        if type(token) is not str or not 32 <= len(token) <= 256 or not all(33 <= ord(c) <= 126 for c in token):
            raise ValueError("invalid_token")
        if type(protocol_version) is not int or protocol_version not in (1, 2, 3, 4, 5):
            raise ValueError("invalid_protocol_version")
        self._version = protocol_version
        self._received_at = None
        self._token = token
        self._runner = None
        self._ws = None
        self._ready = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._pending = {}
        self._state = None
        self._uncertain = False
        self._uncertain_requests = set()
        self._uncertain_overflow = False
        self._resync_epoch = 0
        self._normal_busy = False
        self._closing = False
        self._inventory_binding = None
        self._inventory_metadata = None
        self._inventory_seen = {}

    @property
    def pending_count(self):
        return len(self._pending)

    async def start(self):
        if self._runner is not None or self._closing:
            raise AdapterError("already_started_or_closed")
        app = web.Application(client_max_size=MAX_BYTES)
        app.router.add_get("/minecraft", self._handle)
        self._runner = web.AppRunner(app, access_log=None, shutdown_timeout=1)
        try:
            await self._runner.setup()
            site = web.TCPSite(self._runner, "127.0.0.1", 0)
            await site.start()
            port = self._runner.addresses[0][1]
            return f"ws://127.0.0.1:{port}/minecraft"
        except BaseException:
            await self._runner.cleanup()
            self._runner = None
            raise

    async def wait_connected(self, timeout=3):
        await asyncio.wait_for(self._ready.wait(), timeout)

    def snapshot(self):
        result = copy.deepcopy(self._state) if self._state else {}
        result["connected"] = self._ready.is_set()
        result["resync_required"] = self._uncertain
        if self._version in (2, 3, 4, 5):
            if not result["connected"]:
                result["observation"] = None
            result["received_age_ms"] = (max(0, int((time.monotonic() - self._received_at) * 1000))
                                          if result.get("observation") is not None and self._received_at is not None else None)
        return result

    def _frame(self, kind, **fields):
        if not self._ready.is_set() or self._state is None:
            raise AdapterError("not_connected")
        return {"v": self._version, "type": kind, "session": self._state["session"], "world": self._state["world"], **fields}

    async def _send(self, frame, ws=None):
        target = ws or self._ws
        wire = json.dumps(frame, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        if len(wire.encode("utf-8")) > MAX_BYTES:
            raise AdapterError("message_too_large")
        async with asyncio.timeout(1):
            async with self._send_lock:
                if target is None or target is not self._ws or target.closed:
                    raise AdapterError("outcome_unknown")
                if self._state and (frame["session"], frame["world"]) != (self._state["session"], self._state["world"]):
                    raise AdapterError("stale_binding")
                await target.send_str(wire)

    def _fail_pending(self):
        for future, _, _, _, _ in self._pending.values():
            if not future.done():
                future.set_exception(AdapterError("outcome_unknown"))

    async def _heartbeats(self, ws):
        try:
            while not ws.closed:
                await asyncio.sleep(1)
                await self._send(self._frame("heartbeat"), ws)
        except (AdapterError, TimeoutError, ConnectionError, RuntimeError):
            await ws.close()

    async def _hello(self, frame, ws):
        previous = self._state
        if previous and frame["session"] == previous["session"]:
            if frame["incarnation"] != previous["incarnation"] or frame["next_seq"] < previous["next_seq"]:
                raise AdapterError("invalid_resync")
            if self._version in (2, 3, 4, 5) and previous["observation"] is not None:
                self._check_observation_progress(previous["observation"], frame["observation"], resync=True)
        if self._version == 5 and (not previous or frame["session"] != previous["session"] or previous["observation"] is None):
            self._reset_inventory_pages()
            self._check_inventory_pages(None, frame["observation"])
        self._fail_pending()
        self._ready.clear()
        self._state = copy.deepcopy(frame)
        self._received_at = time.monotonic() if frame["observation"] is not None else None
        self._uncertain = False
        self._uncertain_requests.clear()
        self._uncertain_overflow = False
        self._resync_epoch += 1
        await self._send({"v": self._version, "type": "welcome", "session": frame["session"], "world": frame["world"]}, ws)
        self._ready.set()

    def _check_observation_progress(self, old, new, *, resync=False):
        if self._version == 1:
            if old and (new["seq"] <= old["seq"] or new["tick"] < old["tick"]):
                raise AdapterError("stale_observation")
            return
        if self._version in (3, 4, 5):
            if old is None:
                if self._version == 5:
                    self._check_inventory_pages(None, new)
                return
            if (new is None or new["seq"] < old["seq"] or new["sampled_at_ms"] < old["sampled_at_ms"] or
                    (new["seq"] == old["seq"] and (not resync or new != old))):
                raise AdapterError("stale_observation")
            if new["self"] != old["self"]:
                self._check_player_progress(old["self"], new["self"], resync=resync)
            if new["self"]["generation"] == old["self"]["generation"]:
                if any(new["risk"][key] < old["risk"][key] for key in ("damage_epoch", "monster_epoch")):
                    raise AdapterError("stale_observation")
                if old["motion"] is not None and new["motion"] is not None and new["motion"]["epoch"] < old["motion"]["epoch"]:
                    raise AdapterError("stale_observation")
                if self._version in (4, 5):
                    before = {target["entity_id"]: target for target in old["targets"]}
                    for target in new["targets"]:
                        previous = before.get(target["entity_id"])
                        if previous is None:
                            continue
                        if target["spawn_epoch"] < previous["spawn_epoch"]:
                            raise AdapterError("stale_observation")
                        if target["spawn_epoch"] == previous["spawn_epoch"] and (
                                any(target[key] != previous[key] for key in ("type", "width", "height")) or
                                (previous["dead"] and not target["dead"])):
                            raise AdapterError("stale_observation")
                if self._version == 5:
                    previous, current = old["inventory"], new["inventory"]
                    if current["revision"] < previous["revision"]:
                        raise AdapterError("stale_observation")
                    if current["revision"] == previous["revision"]:
                        if any(current[k] != previous[k] for k in current if k not in ("page_start", "slots")):
                            raise AdapterError("stale_observation")
            if self._version == 5:
                self._check_inventory_pages(old, new)
            return
        self._check_player_progress(old, new, resync=resync)

    def _reset_inventory_pages(self):
        self._inventory_binding = None
        self._inventory_metadata = None
        self._inventory_seen.clear()

    def _check_inventory_pages(self, old, new):
        if new is None:
            self._reset_inventory_pages()
            return
        def binding(observation):
            return (observation["self"]["connection_id"], observation["self"]["generation"], observation["inventory"]["revision"])
        key = binding(new)
        seen = dict(self._inventory_seen) if self._inventory_binding == key else {}
        metadata = self._inventory_metadata if self._inventory_binding == key else None
        for observation in (old, new):
            if observation is None or binding(observation) != key:
                continue
            inventory = observation["inventory"]
            current = {k: v for k, v in inventory.items() if k not in ("page_start", "slots")}
            if metadata is not None and current != metadata:
                raise AdapterError("stale_observation")
            metadata = current
            for slot in inventory["slots"]:
                if slot["index"] in seen and slot != seen[slot["index"]]:
                    raise AdapterError("stale_observation")
                seen[slot["index"]] = copy.deepcopy(slot)
            if len(seen) > 4096:
                raise AdapterError("stale_observation")
        self._inventory_binding = key
        self._inventory_metadata = copy.deepcopy(metadata)
        self._inventory_seen = seen

    @staticmethod
    def _check_player_progress(old, new, *, resync=False):
        if old is None:
            return
        if (new is None or new["connection_id"] != old["connection_id"] or new["generation"] < old["generation"] or
                new["seq"] < old["seq"] or new["event_seq"] < old["event_seq"] or new["observed_at_ms"] < old["observed_at_ms"] or
                (not resync and new["seq"] == old["seq"]) or (new["seq"] == old["seq"] and new != old)):
            raise AdapterError("stale_observation")
        before = old.get("terrain")
        if (new["generation"] == old["generation"] and old["available"] and
                (not new["available"] or new["dimension"] != old["dimension"])):
            raise AdapterError("stale_observation")
        if before is not None and new["generation"] == old["generation"]:
            after = new.get("terrain")
            if (after is None or after["min_y"] != before["min_y"] or after["height"] != before["height"] or
                    after["event_seq"] < before["event_seq"] or after["observed_at_ms"] < before["observed_at_ms"]):
                raise AdapterError("stale_observation")
            if after["event_seq"] == before["event_seq"]:
                cells = {(b["x"], b["y"], b["z"]): b["state_id"] for b in before["blocks"]}
                if after["loaded_chunks"] != before["loaded_chunks"] or any(
                        (b["x"], b["y"], b["z"]) in cells and cells[b["x"], b["y"], b["z"]] != b["state_id"]
                        for b in after["blocks"]):
                    raise AdapterError("stale_observation")

    async def _handle(self, request):
        supplied = request.headers.get("Authorization", "")
        if (self._closing or request.query_string or "Origin" in request.headers or
                request.remote not in {"127.0.0.1", "::1"} or
                not hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + self._token).encode("ascii"))):
            raise web.HTTPForbidden(text="forbidden")
        if self._ws is not None:
            raise web.HTTPConflict(text="peer_already_connected")
        ws = web.WebSocketResponse(max_msg_size=MAX_BYTES, compress=False, autoping=True,
                                   protocols=(f"evelyn.minecraft.v{self._version}",))
        self._ws = ws  # Claim before the handshake await, excluding simultaneous peers.
        heartbeat = None
        try:
            await ws.prepare(request)
            if self._version in (2, 3, 4, 5) and ws.ws_protocol != f"evelyn.minecraft.v{self._version}":
                raise AdapterError("invalid_protocol_version")
            message = await ws.receive(timeout=2)
            if message.type != WSMsgType.TEXT:
                raise AdapterError("invalid_message")
            first = _decode(message.data)
            _validate(first, self._version)
            if first["type"] != "hello":
                raise AdapterError("hello_required")
            await self._hello(first, ws)
            heartbeat = asyncio.create_task(self._heartbeats(ws))
            while not ws.closed:
                message = await ws.receive(timeout=3)
                if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                    break
                if message.type != WSMsgType.TEXT:
                    raise AdapterError("invalid_message")
                frame = _decode(message.data)
                _validate(frame, self._version)
                if frame["type"] == "hello":
                    if frame["session"] != self._state["session"] or frame["incarnation"] != self._state["incarnation"]:
                        raise AdapterError("invalid_resync")
                    await self._hello(frame, ws)
                    continue
                if (frame["session"], frame["world"]) != (self._state["session"], self._state["world"]):
                    raise AdapterError("stale_binding")
                if frame["type"] == "observe":
                    old = self._state["observation"]
                    self._check_observation_progress(old, frame["observation"])
                    self._state["observation"] = frame["observation"]
                    self._received_at = time.monotonic()
                elif frame["type"] == "receipt":
                    self._state["next_seq"] = max(self._state["next_seq"], frame["next_seq"])
                    receipt_keys = ("seq", "status", "code", "evidence") if self._version in (3, 4, 5) else ("seq", "status", "code")
                    record = {key: frame[key] for key in receipt_keys}
                    existing = next((r for r in self._state["receipts"] if r["seq"] == frame["seq"]), None)
                    if existing and ((existing["status"] in TERMINAL and frame["status"] in {"accepted", "running"}) or
                                     (existing["status"] == "running" and frame["status"] == "accepted")):
                        # Correlated replies and cached state must agree on known progress.
                        record = copy.deepcopy(existing)
                        frame = {**frame, **{key: record[key] for key in receipt_keys}}
                    # A rejected duplicate is not the original command's outcome.
                    if frame["status"] != "rejected" or existing is None:
                        records = [r for r in self._state["receipts"] if r["seq"] != frame["seq"]]
                        self._state["receipts"] = (records + [record])[-64:]
                        if frame["status"] in {"accepted", "running"}:
                            self._state["active"] = record
                        elif self._state["active"] and self._state["active"]["seq"] == frame["seq"]:
                            self._state["active"] = None
                    pending = self._pending.get(frame["request_id"])
                    if pending:
                        future, seq, kind, binding, epoch = pending
                        if seq != frame["seq"] or binding != (frame["session"], frame["world"]) or (kind == "cancel" and frame["status"] not in TERMINAL):
                            raise AdapterError("invalid_response")
                        if not future.done():
                            if (kind == "query" and (binding, seq, epoch) in self._uncertain_requests and
                                    frame["status"] in TERMINAL - {"unknown"}):
                                self._uncertain_requests.discard((binding, seq, epoch))
                                self._uncertain = bool(self._uncertain_requests) or self._uncertain_overflow
                            future.set_result(copy.deepcopy(frame))
        except (AdapterError, TimeoutError, ConnectionError, RuntimeError, UnicodeError, TypeError):
            pass
        finally:
            self._ready.clear()
            self._reset_inventory_pages()
            self._uncertain = True
            self._fail_pending()
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            if ws.prepared:
                await ws.close(code=1008, message=b"connection_closed")
            if self._ws is ws:
                self._ws = None
        return ws

    def _mark_uncertain(self, binding, seq, epoch):
        if epoch != self._resync_epoch:
            return
        self._uncertain = True
        request = (binding, seq, epoch)
        if len(self._uncertain_requests) < 64 or request in self._uncertain_requests:
            self._uncertain_requests.add(request)
        else:
            # Lost identities require hello; a partial query cannot prove recovery.
            self._uncertain_overflow = True

    async def _rpc(self, kind, seq, timeout, **fields):
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("invalid_timeout")
        _integer(seq)
        if len(self._pending) >= 2 or (kind != "cancel" and self._normal_busy):
            raise AdapterError("request_busy")
        if kind == "cancel" and any(p[2] == "cancel" for p in self._pending.values()):
            raise AdapterError("request_busy")
        request_id = uuid.uuid4().hex
        frame = self._frame(kind, request_id=request_id, seq=seq, **fields)
        epoch = self._resync_epoch
        binding = (frame["session"], frame["world"])
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (future, seq, kind, binding, epoch)
        if kind != "cancel":
            self._normal_busy = True
        try:
            async with asyncio.timeout(timeout):
                await self._send(frame)
                return await future
        except asyncio.CancelledError:
            if kind != "query":
                self._mark_uncertain(binding, seq, epoch)
            raise
        except (TimeoutError, ConnectionError, RuntimeError):
            if kind != "query":
                self._mark_uncertain(binding, seq, epoch)
            raise AdapterError("outcome_unknown") from None
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Retrieve errors even when cancellation won the race.
            if kind != "cancel":
                self._normal_busy = False

    async def command(self, action, *, args=None, max_age_ms=1000, run_ms=1000, timeout=2):
        if self._version == 2:
            raise AdapterError("read_only")
        self._frame("command")
        if self._uncertain:
            raise AdapterError("resync_required")
        _symbol(action)
        _integer(max_age_ms, 2000)
        _integer(run_ms, 30000)
        if action not in self._state["capabilities"]:
            raise AdapterError("unsupported_action")
        fields = {}
        if self._version in (3, 4, 5):
            if action == "navigate":
                _shape(args, {"target_x", "target_y", "target_z"})
                for key, bound in (("target_x", 30000000), ("target_y", 2048), ("target_z", 30000000)):
                    if self._version in (4, 5) and key == "target_y":
                        if not _finite_number(args[key]) or not -bound <= args[key] <= bound or args[key] * 2 % 1:
                            raise AdapterError("invalid_arguments")
                    elif type(args[key]) is not int or not -bound <= args[key] <= bound:
                        raise AdapterError("invalid_arguments")
            elif action == "look":
                _shape(args, {"yaw", "pitch"})
                for key, bound in (("yaw", 360), ("pitch", 90)):
                    if not _finite_number(args[key]) or not -bound <= args[key] <= bound:
                        raise AdapterError("invalid_arguments")
            elif self._version in (4, 5) and action == "jump":
                if args is not None:
                    _shape(args, set())
                args = {}
            elif self._version in (4, 5) and action == "attack":
                _shape(args, {"target_id", "target_epoch"})
                _nonnegative(args["target_id"])
                _integer(args["target_epoch"])
            elif self._version == 5 and action == "inventory_page":
                _shape(args, {"page_start", "inventory_revision"})
                _nonnegative(args["page_start"], 4095)
                _nonnegative(args["inventory_revision"])
                observed = self._state["observation"]["inventory"] if self._state["observation"] else None
                if observed is None or args["inventory_revision"] != observed["revision"]:
                    raise AdapterError("inventory_changed")
                if args["page_start"] % 16 or (args["page_start"] >= observed["slot_count"] and args["page_start"] != 0):
                    raise AdapterError("invalid_arguments")
            elif self._version == 5 and action == "swap_hands":
                _shape(args, {"inventory_revision"})
                _nonnegative(args["inventory_revision"])
                observed = self._state["observation"]["inventory"] if self._state["observation"] else None
                if observed is None or not observed["known"] or args["inventory_revision"] != observed["revision"]:
                    raise AdapterError("inventory_changed")
                if observed["container_id"] != 0 or observed["menu_type_id"] is not None or observed["slot_count"] != 46 or observed["selected"] is None:
                    raise AdapterError("invalid_arguments")
                key = (self._state["observation"]["self"]["connection_id"], self._state["observation"]["self"]["generation"], observed["revision"])
                seen = self._inventory_seen if self._inventory_binding == key else {}
                main = 36 + observed["selected"]
                if main not in seen or 45 not in seen:
                    raise AdapterError("inventory_slots_required")
                if observed["carried"]["count"] != 0 or seen[main]["count"] == seen[45]["count"] == 0:
                    raise AdapterError("invalid_arguments")
            elif self._version == 5 and action == "inventory_transfer":
                _shape(args, {"source_slot", "destination_slot", "amount", "inventory_revision"})
                for key in ("source_slot", "destination_slot"):
                    _nonnegative(args[key], 4095)
                _integer(args["amount"], 99)
                _nonnegative(args["inventory_revision"])
                observed = self._state["observation"]["inventory"] if self._state["observation"] else None
                if observed is None or not observed["known"] or args["inventory_revision"] != observed["revision"]:
                    raise AdapterError("inventory_changed")
                source, destination = args["source_slot"], args["destination_slot"]
                key = (self._state["observation"]["self"]["connection_id"], self._state["observation"]["self"]["generation"], observed["revision"])
                seen = self._inventory_seen if self._inventory_binding == key else {}
                if source == destination or source not in seen or destination not in seen:
                    raise AdapterError("inventory_slots_required")
                if observed["container_id"] == 0:
                    if not (9 <= source < 45 and 9 <= destination < 45) or observed["menu_type_id"] is not None:
                        raise AdapterError("invalid_arguments")
                elif observed["menu_type_id"] not in range(7):
                    raise AdapterError("invalid_arguments")
                if seen[source]["count"] < args["amount"] or seen[destination]["count"] != 0 or observed["carried"]["count"] != 0:
                    raise AdapterError("invalid_arguments")
            else:
                raise AdapterError("unsupported_action")
            fields = dict(args)
        elif args is not None:
            raise AdapterError("invalid_arguments")
        observation = self._state["observation"]
        if observation is None:
            raise AdapterError("observation_required")
        return await self._rpc("command", self._state["next_seq"], timeout, action=action,
                               observation_seq=observation["seq"], max_age_ms=max_age_ms, run_ms=run_ms, **fields)

    async def navigate(self, x, y, z, *, max_age_ms=1000, run_ms=1000, timeout=2):
        if self._version not in (3, 4, 5):
            raise AdapterError("read_only" if self._version == 2 else "control_mode_required")
        return await self.command("navigate", args={"target_x": x, "target_y": y, "target_z": z},
                                  max_age_ms=max_age_ms, run_ms=run_ms, timeout=timeout)

    async def look(self, yaw, pitch, *, max_age_ms=1000, run_ms=1000, timeout=2):
        if self._version not in (3, 4, 5):
            raise AdapterError("read_only" if self._version == 2 else "control_mode_required")
        return await self.command("look", args={"yaw": yaw, "pitch": pitch},
                                  max_age_ms=max_age_ms, run_ms=run_ms, timeout=timeout)

    async def jump(self, *, max_age_ms=1000, run_ms=1000, timeout=2):
        if self._version not in (4, 5):
            raise AdapterError("read_only" if self._version == 2 else "advanced_mode_required")
        return await self.command("jump", max_age_ms=max_age_ms, run_ms=run_ms, timeout=timeout)

    async def attack(self, target_id, target_epoch, *, max_age_ms=1000, run_ms=1000, timeout=2):
        if self._version not in (4, 5):
            raise AdapterError("read_only" if self._version == 2 else "advanced_mode_required")
        return await self.command("attack", args={"target_id": target_id, "target_epoch": target_epoch},
                                  max_age_ms=max_age_ms, run_ms=run_ms, timeout=timeout)

    async def inventory_page(self, page_start=0, *, expected_revision=None, max_age_ms=1000, run_ms=1000, timeout=2):
        if self._version != 5:
            raise AdapterError("player_mode_required")
        self._frame("command")
        observed = self._state["observation"]
        if observed is None:
            raise AdapterError("observation_required")
        revision = observed["inventory"]["revision"] if expected_revision is None else expected_revision
        return await self.command("inventory_page", args={"page_start": page_start, "inventory_revision": revision},
                                  max_age_ms=max_age_ms, run_ms=run_ms, timeout=timeout)

    async def swap_hands(self, *, expected_revision, max_age_ms=1000, run_ms=10000, timeout=2):
        if self._version != 5:
            raise AdapterError("player_mode_required")
        return await self.command("swap_hands", args={"inventory_revision": expected_revision},
                                  max_age_ms=max_age_ms, run_ms=run_ms, timeout=timeout)

    async def inventory_transfer(self, source_slot, destination_slot, amount, *, expected_revision, max_age_ms=1000, run_ms=10000, timeout=2):
        if self._version != 5:
            raise AdapterError("player_mode_required")
        return await self.command("inventory_transfer", args={"source_slot": source_slot, "destination_slot": destination_slot,
                                  "amount": amount, "inventory_revision": expected_revision}, max_age_ms=max_age_ms, run_ms=run_ms, timeout=timeout)

    async def off(self):
        if self._version not in (3, 4, 5):
            raise AdapterError("read_only" if self._version == 2 else "control_mode_required")
        try:
            frame = self._frame("off", request_id=uuid.uuid4().hex)
            self._closing = True
            self._ready.clear()
            self._uncertain = True
            await self._send(frame)
        finally:
            await self.close()

    def _binding(self, session, world):
        self._frame("query")
        if (session, world) != (self._state["session"], self._state["world"]):
            raise AdapterError("stale_binding")

    async def query(self, seq, *, session, world, timeout=2):
        if self._version == 2:
            raise AdapterError("read_only")
        self._binding(session, world)
        return await self._rpc("query", seq, timeout)

    async def cancel(self, seq, *, session, world, timeout=2):
        if self._version == 2:
            raise AdapterError("read_only")
        self._binding(session, world)
        return await self._rpc("cancel", seq, timeout)

    async def close(self):
        self._closing = True
        self._ready.clear()
        self._reset_inventory_pages()
        self._fail_pending()
        if self._ws is not None:
            await self._ws.close()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
