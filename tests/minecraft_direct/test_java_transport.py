"""Compile real Java 25 peer and exercise synthetic loopback only, without Minecraft.

Run with --java-home <installed JDK> --gson <installed Gson jar>. No downloads.
"""
import argparse
import asyncio
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
oracle_spec = importlib.util.spec_from_file_location("menu_property_oracle", Path(__file__).with_name("menu_property_oracle.py"))
oracle = importlib.util.module_from_spec(oracle_spec)
oracle_spec.loader.exec_module(oracle)
check_java_output = oracle.check_java_output
spec = importlib.util.spec_from_file_location("direct", ROOT / "evelyn_core/runtime/evelyn_core/minecraft_direct_adapter.py")
direct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(direct)


class JavaTransportTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="evelyn-direct-java-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
        cls.env.update(TEMP=cls.temp.name, TMP=cls.temp.name)
        cls.java = str(Path(OPTIONS.java_home) / "bin/java.exe")
        compiler = str(Path(OPTIONS.java_home) / "bin/javac.exe")
        gson = Path(OPTIONS.gson).resolve(strict=True)
        sources = sorted((ROOT / "adapters/minecraft-client/src").rglob("*.java"))
        if not sources or not Path(cls.java).is_file():
            raise RuntimeError("java_fixture_inputs_missing")
        command = [compiler, "--release", "25", "-encoding", "UTF-8", "-cp", str(gson),
                   "-d", cls.temp.name, *map(str, sources)]
        compiled = subprocess.run(command, env=cls.env, cwd=cls.temp.name, capture_output=True, timeout=30)
        if compiled.returncode:
            raise AssertionError(compiled.stderr.decode("utf-8", errors="replace"))
        resources = (ROOT / "adapters/minecraft-client/src/main/resources").resolve(strict=True)
        cls.classpath = os.pathsep.join((cls.temp.name, str(gson), str(resources)))
        for case in ("ProtocolTest", "SessionEngineTest", "DirectClientTest",
                     "MicrosoftAuthTest", "VanillaConnectionTest", "VanillaBotTest",
                     "PlayerObservationTest", "VanillaObservationTest", "ChunkWorldTest",
                     "ChunkCodecTest", "VanillaWorldTest", "BlockCatalogTest", "EntityTrackerTest",
                     "MovementControllerTest", "LocalPlannerTest", "VanillaControlTest",
                     "ControlProtocolTest", "ControlSessionTest", "TerrainCollisionTest",
                     "BareHandInventoryTest", "CombatControllerTest", "CombatTargetTest",
                     "AdvancedProtocolTest", "AdvancedSessionTest", "AdvancedControlTest", "AdvancedBoundaryTest",
                     "InventoryStateTest", "MenuLayoutObservationTest", "MenuPropertiesTest", "TagStateTest", "CurrentTagControlTest",
                     "BlockInteractionStateTest", "BlockObservationControlTest", "PlayerActionPacketsTest", "ItemCatalogTest", "MenuPacketsTest",
                     "PlayerAttributesTest", "PlayerAttributesControlTest", "ComponentDecodeSessionTest",
                     "RecipeStateTest", "RecipeControlTest", "MiningReferenceTest",
                     "InventoryTransferTest", "PlayerInventoryControlTest", "PlayerInventoryServerTest",
                     "ComponentWireCodecTest", "ComponentHashTest", "ComponentRegistryKeysTest", "InventoryComponentsTest",
                     "PlayerProtocolTest", "PlayerControlTest", "HandSwapTest", "HandSwapProtocolTest",
                     "HandSwapControlTest", "PlayerHandSwapServerTest"):
            checked = subprocess.run([cls.java, "-cp", cls.classpath, "dev.evelyn.minecraft." + case],
                                     env=cls.env, cwd=cls.temp.name, capture_output=True, timeout=15)
            if checked.returncode:
                raise AssertionError(checked.stderr.decode("utf-8", errors="replace"))
            output = checked.stdout.decode("utf-8").strip()
            if case == "MenuPropertiesTest":
                check_java_output(output, direct._inventory_observation)
                output = "\n".join(line for line in output.splitlines()
                                   if not line.startswith("MENU_PROPERTY_OBSERVATIONS "))
            print(output)

    async def asyncSetUp(self):
        self.player_mode = self._testMethodName.startswith("test_player_")
        self.observer_mode = self._testMethodName.startswith("test_observer_")
        self.advanced_mode = self._testMethodName.startswith("test_advanced_")
        self.control_mode = self.player_mode or self.advanced_mode or self._testMethodName.startswith("test_control_")
        self.adapter = direct.DirectAdapter("synthetic-java-test-token-" + "a" * 32,
                                            protocol_version=5 if self.player_mode else 4 if self.advanced_mode else 3 if self.control_mode else 2 if self.observer_mode else 1)
        self.addAsyncCleanup(self.adapter.close)
        endpoint = await self.adapter.start()
        env = {**self.env, "EVELYN_TEST_ENDPOINT": endpoint,
               "EVELYN_TEST_TOKEN": "synthetic-java-test-token-" + "a" * 32,
               "EVELYN_TEST_DURATION_MS": "25000"}
        if self._testMethodName.startswith("test_observer_terrain_"):
            env["EVELYN_TEST_TERRAIN"] = "1"
        fixture = "PlayerControlFixture" if self.player_mode else "AdvancedControlFixture" if self.advanced_mode else "ControlFixture" if self.control_mode else "ObserverFixture" if self.observer_mode else "TransportFixture"
        self.proc = await asyncio.create_subprocess_exec(
            self.java, "-Djava.net.useSystemProxies=false", "-cp", self.classpath,
            "dev.evelyn.minecraft." + fixture, env=env, cwd=self.temp.name,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.addAsyncCleanup(self.cleanup)
        await self.adapter.wait_connected(5)
        if self.observer_mode or self.control_mode:
            self.assertEqual((await asyncio.wait_for(self.proc.stdout.readline(), 5)).rstrip(b"\r\n"), b"READY")
        if self.control_mode:
            await self.until(lambda: self.adapter.snapshot().get("observation") is not None)

    async def cleanup(self):
        if self.proc.returncode is None:
            try:
                await self.control("quit")
            except (BrokenPipeError, ConnectionResetError):
                pass
        try:
            stdout, stderr = await asyncio.wait_for(self.proc.communicate(), 6)
        except TimeoutError:
            if self.proc.returncode is None:
                self.proc.terminate()
            await self.proc.wait()
            self.fail("owned_fixture_shutdown_timeout")
        self.assertEqual(self.proc.returncode, 0, stderr.decode("utf-8", errors="replace"))
        self.assertNotIn(b"synthetic-java-test-token", stdout + stderr)
        if self.observer_mode:
            self.assertIn(b"OBSERVER_CLOSED", stdout)
        if self.control_mode:
            self.assertIn(b"CONTROL_CLOSED", stdout)
        if hasattr(self, "expected_inventory_server"):
            self.assertIn(self.expected_inventory_server, stdout)
        if hasattr(self, "expected_hand_swap_server"):
            self.assertIn(self.expected_hand_swap_server, stdout)

    async def test_player_inventory_pages_preserve_receiver_and_revision(self):
        self.assertFalse(self.adapter.snapshot()["observation"]["inventory"]["known"])
        await self.control("player_inventory")
        await self.until(lambda: self.adapter.snapshot()["observation"]["inventory"]["known"])
        before = self.adapter.snapshot()["observation"]["inventory"]
        result = await self.adapter.inventory_page(32, expected_revision=before["revision"])
        result = await self.terminal(result["seq"], {"session": result["session"], "world": result["world"]})
        self.assertEqual((result["status"], result["code"], result["evidence"]),
                         ("succeeded", "inventory_page_selected", "client_simulated"))
        await self.until(lambda: self.adapter.snapshot()["observation"]["inventory"]["page_start"] == 32)
        current = self.adapter.snapshot()["observation"]["inventory"]
        self.assertEqual((len(current["slots"]), current["slots"][4]["index"], current["slots"][4]["item_id"], current["slots"][4]["count"]), (14, 36, 1, 8))
        self.assertEqual(current["revision"], before["revision"])
        await self.control("player_slot")
        await self.until(lambda: self.adapter.snapshot()["observation"]["inventory"]["revision"] > before["revision"])
        self.assertEqual(self.adapter.snapshot()["observation"]["inventory"]["slots"][4]["count"], 7)
        with self.assertRaises(direct.AdapterError):
            await self.adapter.inventory_page(0, expected_revision=before["revision"])

    async def test_player_disconnect_discards_known_inventory(self):
        await self.control("player_inventory")
        await self.until(lambda: self.adapter.snapshot()["observation"]["inventory"]["known"])
        await self.adapter.close()
        await self.until(lambda: self.proc.returncode is not None)
        self.assertIsNone(self.adapter.snapshot()["observation"])
        self.assertEqual(self.adapter.pending_count, 0)
        with self.assertRaises(direct.AdapterError):
            await self.adapter.inventory_page()

    async def test_player_special_menu_pages_and_mount_invalidation(self):
        await self.control_update("player_crafter")
        await self.until(lambda: self.control_observation()["inventory"]["menu_type_id"] == 7)
        current = self.control_observation()["inventory"]
        self.assertEqual((current["known"], current["slot_count"]), (True, 46))
        receipt = await self.adapter.inventory_page(32, expected_revision=current["revision"])
        result = await self.terminal(receipt["seq"], {"session": receipt["session"], "world": receipt["world"]})
        self.assertEqual(result["status"], "succeeded")
        await self.until(lambda: self.control_observation()["inventory"]["page_start"] == 32)
        current = self.control_observation()["inventory"]
        self.assertEqual((current["slots"][-1]["index"], current["slots"][-1]["count"]), (45, 1))
        with self.assertRaises(direct.AdapterError):
            await self.adapter.inventory_transfer(36, 37, 1, expected_revision=current["revision"])
        await self.control_update("player_lectern")
        await self.until(lambda: self.control_observation()["inventory"]["menu_type_id"] == 17)
        current = self.control_observation()["inventory"]
        self.assertEqual((current["known"], current["slot_count"], current["page_start"], len(current["slots"])), (True, 1, 0, 1))
        with self.assertRaises(direct.AdapterError):
            await self.adapter.inventory_page(16, expected_revision=current["revision"])
        await self.control_update("player_mount")
        await self.until(lambda: not self.control_observation()["inventory"]["known"])
        current = self.control_observation()["inventory"]
        self.assertEqual((current["slot_count"], current["page_start"], current["slots"], current["carried"]), (0, 0, [], None))
        with self.assertRaises(direct.AdapterError):
            await self.adapter.inventory_transfer(0, 1, 1, expected_revision=current["revision"])

    async def test_player_menu_properties_are_received_without_default_zero(self):
        await self.control_update("player_furnace")
        await self.until(lambda: self.control_observation()["inventory"]["known"]
                         and self.control_observation()["inventory"]["container_id"] == 1)
        current = self.control_observation()["inventory"]
        self.assertEqual((current["container_id"], current["menu_type_id"], current["state_id"], current["properties"]), (1, 14, 5, [None] * 4))
        revision = current["revision"]
        await self.control_update("player_property")
        await self.until(lambda: self.control_observation()["inventory"]["revision"] >= revision + 3)
        current = self.control_observation()["inventory"]
        self.assertGreater(current["revision"], revision)
        self.assertEqual((current["state_id"], current["properties"]), (5, [-32768, 0, 32767, None]))
        await self.control_update("player_smithing")
        await self.until(lambda: self.control_observation()["inventory"]["known"]
                         and self.control_observation()["inventory"]["menu_type_id"] == 21)
        current = self.control_observation()["inventory"]
        self.assertEqual(current["properties"], [None])
        revision = current["revision"]
        await self.control_update("player_smith_error")
        await self.until(lambda: self.control_observation()["inventory"]["revision"] > revision)
        self.assertEqual(self.control_observation()["inventory"]["properties"], [1])
        await self.control_update("player_cartography")
        await self.until(lambda: self.control_observation()["inventory"]["known"]
                         and self.control_observation()["inventory"]["menu_type_id"] == 23)
        self.assertEqual(self.control_observation()["inventory"]["properties"], [])

    async def test_player_recipe_receiver_and_menu_lifetimes(self):
        # Raw recipe state is currently internal. This fixture marker checks that receiver,
        # while the normal adapter session continues to observe inventory and page receipts.
        async def report(expected):
            await self.control("player_recipe_report")
            self.assertEqual((await asyncio.wait_for(self.proc.stdout.readline(), 3)).rstrip(b"\r\n"), expected)
            self.assertEqual((await asyncio.wait_for(self.proc.stdout.readline(), 3)).rstrip(b"\r\n"), b"UPDATE")
        await self.control_update("player_recipe_add")
        await self.control_update("player_furnace")
        await self.until(lambda: self.control_observation()["inventory"]["known"])
        await self.control_update("player_recipe_ghost")
        await report(b"RECIPES complete=true ids=1 id7=true ghost=1 settings=4")
        await self.control_update("player_smithing")
        await self.until(lambda: self.control_observation()["inventory"]["menu_type_id"] == 21)
        await report(b"RECIPES complete=true ids=1 id7=true ghost=unknown settings=4")
        inventory = self.control_observation()["inventory"]
        receipt = await self.adapter.inventory_page(32, expected_revision=inventory["revision"])
        terminal = await self.terminal(receipt["seq"], {"session": receipt["session"], "world": receipt["world"]})
        self.assertEqual(terminal["status"], "succeeded")
        await self.control_update("player_recipe_properties")
        await report(b"RECIPES complete=false ids=0 id7=false ghost=unknown settings=4")

    async def player_transfer_ready(self):
        await self.control_update("player_transfer_server")
        await self.until(lambda: self.control_observation()["inventory"]["known"])
        initial = self.control_observation()["inventory"]
        receipt = await self.adapter.inventory_page(32, expected_revision=initial["revision"])
        selected = await self.terminal(receipt["seq"], {"session": receipt["session"], "world": receipt["world"]})
        self.assertEqual((selected["status"], selected["code"]), ("succeeded", "inventory_page_selected"))
        await self.until(lambda: self.control_observation()["inventory"]["page_start"] == 32)
        inventory = self.control_observation()["inventory"]
        self.assertEqual((inventory["slots"][4]["index"], inventory["slots"][4]["count"],
                          inventory["slots"][5]["index"], inventory["slots"][5]["count"], inventory["carried"]["count"]),
                         (36, 8, 37, 0, 0))
        return inventory["revision"]

    async def player_swap_ready(self):
        await self.control_update("player_swap_server")
        await self.until(lambda: self.control_observation()["inventory"]["known"])
        revision = self.control_observation()["inventory"]["revision"]
        receipt = await self.adapter.inventory_page(32, expected_revision=revision)
        binding = {"session": receipt["session"], "world": receipt["world"]}
        self.assertEqual((await self.terminal(receipt["seq"], binding))["status"], "succeeded")
        await self.until(lambda: self.control_observation()["inventory"]["page_start"] == 32)
        inventory = self.control_observation()["inventory"]
        self.assertEqual((inventory["slots"][4]["item_id"], inventory["slots"][4]["count"],
                          inventory["slots"][13]["item_id"], inventory["slots"][13]["count"], inventory["selected"]), (1, 8, 2, 3, 0))
        return inventory["revision"]

    async def test_player_hand_swap_observes_both_server_hands(self):
        revision = await self.player_swap_ready()
        receipt = await self.adapter.swap_hands(expected_revision=revision)
        binding = {"session": receipt["session"], "world": receipt["world"]}
        self.assertIn(receipt["status"], {"accepted", "running"})
        self.assertEqual(receipt["evidence"], "none")
        result = await self.terminal(receipt["seq"], binding)
        self.assertEqual((result["status"], result["code"], result["evidence"]),
                         ("succeeded", "hand_swap_observed", "server_observed"))
        await self.until(lambda: self.control_observation()["inventory"]["slots"][4]["item_id"] == 2)
        inventory = self.control_observation()["inventory"]
        self.assertGreater(inventory["revision"], revision)
        self.assertEqual((inventory["slots"][4]["item_id"], inventory["slots"][4]["count"],
                          inventory["slots"][13]["item_id"], inventory["slots"][13]["count"]), (2, 3, 1, 8))
        self.expected_hand_swap_server = b"PLAYER_SWAP_SERVER 5 2 3 1 8 1 false"

    async def test_player_hand_swap_pending_cancel_never_replays(self):
        revision = await self.player_swap_ready()
        await self.control_update("player_swap_pause")
        receipt = await self.adapter.swap_hands(expected_revision=revision)
        binding = {"session": receipt["session"], "world": receipt["world"]}
        await asyncio.sleep(0.15)
        await self.control("player_swap_report")
        self.assertEqual((await asyncio.wait_for(self.proc.stdout.readline(), 3)).rstrip(b"\r\n"),
                         b"PLAYER_SWAP_SERVER 5 2 3 1 8 1 true")
        self.assertEqual((await asyncio.wait_for(self.proc.stdout.readline(), 3)).rstrip(b"\r\n"), b"UPDATE")
        current = await self.adapter.query(receipt["seq"], **binding)
        self.assertEqual((current["status"], current["evidence"]), ("running", "none"))
        inventory = self.control_observation()["inventory"]
        self.assertEqual((inventory["revision"], inventory["slots"][4]["item_id"], inventory["slots"][13]["item_id"]), (revision, 1, 2))
        self.expected_hand_swap_server = b"PLAYER_SWAP_SERVER 5 2 3 1 8 1 true"
        try:
            result = await self.adapter.cancel(receipt["seq"], **binding)
            self.assertIn(result["status"], {"cancelled", "unknown"})
        except direct.AdapterError as failure:
            self.assertIn("outcome_unknown", str(failure))
        await self.until(lambda: self.proc.returncode is not None, seconds=4)
        self.assertFalse(self.adapter.snapshot()["connected"])
        self.assertIsNone(self.adapter.snapshot()["observation"])
        self.assertEqual(self.adapter.pending_count, 0)

    async def player_transfer_complete(self, amount):
        revision = await self.player_transfer_ready()
        receipt = await self.adapter.inventory_transfer(36, 37, amount, expected_revision=revision)
        binding = {"session": receipt["session"], "world": receipt["world"]}
        self.assertIn(receipt["status"], {"accepted", "running"})
        self.assertEqual(receipt["evidence"], "none")
        result = await self.terminal(receipt["seq"], binding)
        self.assertEqual((result["seq"], result["session"], result["world"]),
                         (receipt["seq"], binding["session"], binding["world"]))
        self.assertEqual((result["status"], result["code"], result["evidence"]),
                         ("succeeded", "inventory_transfer_observed", "server_observed"))
        await self.until(lambda: self.control_observation()["inventory"]["slots"][5]["count"] == amount)
        inventory = self.control_observation()["inventory"]
        self.assertGreater(inventory["revision"], revision)
        self.assertEqual((inventory["slots"][4]["count"], inventory["slots"][5]["count"], inventory["carried"]["count"]),
                         (8 - amount, amount, 0))
        self.expected_inventory_server = (b"PLAYER_SERVER 5 0 8 0 2 false" if amount == 8 else
                                          b"PLAYER_SERVER 8 5 3 0 5 false")

    async def test_player_inventory_transfer_full_observes_server_effect(self):
        await self.player_transfer_complete(8)

    async def test_player_inventory_transfer_partial_observes_server_effect(self):
        await self.player_transfer_complete(3)

    async def test_player_inventory_transfer_pending_cancel_closes_without_replay(self):
        revision = await self.player_transfer_ready()
        await self.control_update("player_transfer_pause")
        receipt = await self.adapter.inventory_transfer(36, 37, 3, expected_revision=revision)
        binding = {"session": receipt["session"], "world": receipt["world"]}
        self.assertIn(receipt["status"], {"accepted", "running"})
        self.assertEqual(receipt["evidence"], "none")
        await asyncio.sleep(0.15)
        await self.control("player_transfer_report")
        self.assertEqual((await asyncio.wait_for(self.proc.stdout.readline(), 3)).rstrip(b"\r\n"),
                         b"PLAYER_SERVER 4 0 0 8 1 true")
        self.assertEqual((await asyncio.wait_for(self.proc.stdout.readline(), 3)).rstrip(b"\r\n"), b"UPDATE")
        running = await self.adapter.query(receipt["seq"], **binding)
        self.assertEqual((running["status"], running["evidence"]), ("running", "none"))
        inventory = self.control_observation()["inventory"]
        self.assertEqual((inventory["revision"], inventory["slots"][4]["count"], inventory["carried"]["count"]), (revision, 8, 0))
        self.expected_inventory_server = b"PLAYER_SERVER 4 0 0 8 1 true"
        try:
            cancelled = await self.adapter.cancel(receipt["seq"], **binding)
            self.assertIn(cancelled["status"], {"cancelled", "unknown"})
            self.assertEqual(cancelled["evidence"], "none")
        except direct.AdapterError as failure:
            self.assertIn("outcome_unknown", str(failure))
        await self.until(lambda: self.proc.returncode is not None, seconds=4)
        self.assertFalse(self.adapter.snapshot()["connected"])
        self.assertIsNone(self.adapter.snapshot()["observation"])
        self.assertEqual(self.adapter.pending_count, 0)
        with self.assertRaises(direct.AdapterError):
            await self.adapter.inventory_transfer(36, 37, 3, expected_revision=revision)

    async def test_observer_player_update_invalidation_and_disconnect(self):
        before = self.adapter.snapshot()["observation"]
        self.assertEqual(before["health"]["health"], 20)
        self.assertEqual(before["position"]["source"], "server_correction")
        self.assertFalse(before["world_ready"])
        await self.control("health")
        await self.until(lambda: self.adapter.snapshot()["observation"]["health"]["health"] == 19)
        await self.control("world")
        await self.until(lambda: self.adapter.snapshot()["observation"]["generation"] > before["generation"])
        current = self.adapter.snapshot()["observation"]
        self.assertFalse(current["available"])
        self.assertIsNone(current["position"])
        self.assertIsNone(current["health"])
        await self.adapter.close()
        await self.until(lambda: self.proc.returncode is not None)
        self.assertIsNone(self.adapter.snapshot()["observation"])

    async def test_observer_wire_action_is_rejected_without_execution(self):
        state = self.adapter.snapshot()
        await self.adapter._send({"v": 2, "type": "command", "session": state["session"], "world": state["world"],
                                  "request_id": "forbidden", "seq": 1, "action": "walk", "observation_seq": 1,
                                  "max_age_ms": 1000, "run_ms": 1000})
        await self.until(lambda: self.proc.returncode is not None)
        self.assertFalse(self.adapter.snapshot()["connected"])
        self.assertIsNone(self.adapter.snapshot()["observation"])

    async def test_observer_terrain_updates_bundle_unload_and_stop(self):
        def cells(observation):
            return {(block["x"], block["y"], block["z"]): block["state_id"]
                    for block in observation["terrain"]["blocks"]}

        async def updated(command, marker=b"UPDATE"):
            await self.control(command)
            self.assertEqual((await asyncio.wait_for(self.proc.stdout.readline(), 3)).rstrip(b"\r\n"), marker)

        before = self.adapter.snapshot()["observation"]
        self.assertIn("terrain", before, "terrain fixture must publish a typed world sample on its first hello")
        known, absent, outside = (-1, 64, -1), (-17, 64, -1), (-1, -65, -1)
        self.assertEqual(cells(before), {known: 0, absent: None, outside: None})
        self.assertEqual(before["terrain"]["encoding"], "opaque_state_id")
        self.assertEqual((before["terrain"]["min_y"], before["terrain"]["height"],
                          before["terrain"]["loaded_chunks"]), (-64, 256, 1))
        self.assertEqual(before["coverage"], {"chunks": "partial", "entities": "unknown", "inventory": "unknown"})
        self.assertFalse(before["world_ready"])
        self.assertEqual(self.adapter.snapshot()["capabilities"], [])

        await updated("terrain")
        await self.until(lambda: cells(self.adapter.snapshot()["observation"])[known] == 17)
        changed = self.adapter.snapshot()["observation"]
        self.assertGreater(changed["seq"], before["seq"])
        self.assertGreater(changed["event_seq"], before["event_seq"])
        self.assertGreater(changed["terrain"]["event_seq"], before["terrain"]["event_seq"])
        self.assertEqual(changed["health"], before["health"])
        self.assertEqual(changed["position"], before["position"])
        self.assertIsNone(cells(changed)[absent])

        await updated("bundle_start", b"BUNDLE_STAGED")
        await asyncio.sleep(0.08)
        self.assertEqual(self.adapter.snapshot()["observation"], changed,
                         "incomplete bundle must not publish either its terrain or self changes")
        await updated("bundle_end")
        await self.until(lambda: self.adapter.snapshot()["observation"]["seq"] > changed["seq"])
        bundled = self.adapter.snapshot()["observation"]
        self.assertEqual(bundled["seq"], changed["seq"] + 1)
        self.assertEqual(cells(bundled)[known], 23)
        self.assertEqual(bundled["health"]["health"], 18)
        self.assertGreater(bundled["event_seq"], changed["event_seq"])
        self.assertLessEqual(bundled["terrain"]["event_seq"], bundled["event_seq"])

        await updated("unload")
        await self.until(lambda: self.adapter.snapshot()["observation"]["terrain"]["loaded_chunks"] == 0)
        unloaded = self.adapter.snapshot()["observation"]
        self.assertEqual(cells(unloaded), {known: None, absent: None, outside: None})
        self.assertGreater(unloaded["seq"], bundled["seq"])
        self.assertEqual(unloaded["health"], bundled["health"])

        await updated("world")
        await self.until(lambda: self.adapter.snapshot()["observation"]["generation"] > before["generation"])
        invalidated = self.adapter.snapshot()["observation"]
        self.assertFalse(invalidated["available"])
        self.assertNotIn("terrain", invalidated)
        self.assertEqual(invalidated["coverage"]["chunks"], "unknown")
        self.assertIsNone(invalidated["position"])
        self.assertIsNone(invalidated["health"])
        await self.adapter.close()
        await self.until(lambda: self.proc.returncode is not None)
        self.assertIsNone(self.adapter.snapshot()["observation"])

    async def control(self, command):
        self.proc.stdin.write((command + "\n").encode("ascii"))
        await self.proc.stdin.drain()

    async def until(self, predicate, seconds=3):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("synthetic_condition_timeout")

    def binding(self):
        snapshot = self.adapter.snapshot()
        return {"session": snapshot["session"], "world": snapshot["world"]}

    async def terminal(self, seq, binding):
        for _ in range(500 if self.control_mode else 100):
            result = await self.adapter.query(seq, **binding)
            if result["status"] not in {"accepted", "running"}:
                return result
            await asyncio.sleep(0.01)
        self.fail("terminal_timeout")

    async def control_update(self, command):
        await self.control(command)
        self.assertEqual((await asyncio.wait_for(self.proc.stdout.readline(), 3)).rstrip(b"\r\n"), b"UPDATE")

    def control_observation(self):
        return self.adapter.snapshot().get("observation") or {}

    async def running_navigation(self, x=14, y=1, z=8):
        receipt = await self.adapter.navigate(x, y, z, run_ms=10000)
        self.assertIn(receipt["status"], {"accepted", "running"})
        self.assertEqual(receipt["evidence"], "none")
        await self.until(lambda: self.control_observation().get("planner", {}).get("goal_seq") == receipt["seq"]
                         and self.control_observation()["planner"]["step_id"] > 0)
        return receipt

    async def test_control_navigate_and_look_use_simulated_evidence(self):
        before = self.control_observation()
        original_position = before["self"]["position"]
        self.assertEqual((original_position["x"], original_position["y"], original_position["z"]), (8.5, 1., 8.5))
        self.assertEqual(original_position["source"], "server_correction")
        self.assertFalse(before["self"]["world_ready"])
        self.assertEqual(self.adapter.snapshot()["capabilities"], ["look", "navigate"])
        receipt = await self.adapter.navigate(10, 1, 8, run_ms=10000)
        result = await self.terminal(receipt["seq"], self.binding())
        self.assertEqual((result["status"], result["evidence"]), ("succeeded", "client_simulated"))
        def arrived():
            motion = self.control_observation().get("motion")
            return motion is not None and math.dist((motion["x"], motion["y"], motion["z"]), (10.5, 1., 8.5)) <= .15 + 1e-7
        await self.until(arrived)
        after = self.control_observation()
        self.assertEqual(after["self"]["position"], original_position,
                         "local prediction must not rewrite server-observed corrections")
        self.assertEqual(after["motion"]["source"], "client_simulated")
        self.assertLessEqual(math.hypot(after["motion"]["vx"], after["motion"]["vz"]), .003 + 1e-7)
        receipt = await self.adapter.look(45, -30, run_ms=3000)
        result = await self.terminal(receipt["seq"], self.binding())
        self.assertEqual((result["status"], result["evidence"]), ("succeeded", "client_simulated"))
        await self.until(lambda: self.control_observation().get("motion") is not None
                         and abs(self.control_observation()["motion"]["yaw"] - 45) < 1e-5
                         and abs(self.control_observation()["motion"]["pitch"] + 30) < 1e-5)
        self.assertEqual(self.control_observation()["self"]["position"], original_position)

    async def test_control_damage_heal_blink_pauses_and_resumes_new_step(self):
        receipt = await self.running_navigation()
        before = self.control_observation()
        step = before["planner"]["step_id"]
        await self.control_update("damageheal")
        await self.until(lambda: self.control_observation().get("planner", {}).get("paused") is True)
        paused = self.control_observation()
        self.assertGreater(paused["risk"]["damage_epoch"], before["risk"]["damage_epoch"])
        self.assertEqual(paused["self"]["health"]["health"], 20,
                         "the damage event must survive heal coalescing")
        self.assertGreater(paused["planner"]["preemptions"], before["planner"]["preemptions"])
        await self.until(lambda: self.control_observation().get("planner", {}).get("paused") is False
                         and self.control_observation()["planner"]["step_id"] > step, seconds=5)
        result = await self.terminal(receipt["seq"], self.binding())
        self.assertEqual((result["status"], result["evidence"]), ("succeeded", "client_simulated"))

    async def test_control_monster_pause_requires_clearance_and_fresh_step(self):
        receipt = await self.running_navigation()
        before = self.control_observation()
        step = before["planner"]["step_id"]
        await self.control_update("monster")
        await self.until(lambda: self.control_observation().get("risk", {}).get("near_monster") is True
                         and self.control_observation()["planner"]["paused"])
        threat = self.control_observation()
        self.assertGreater(threat["risk"]["monster_epoch"], before["risk"]["monster_epoch"])
        self.assertEqual(threat["risk"]["coverage"], "server_tracked_partial")
        self.assertGreaterEqual(threat["risk"]["tracked_count"], 1)
        await asyncio.sleep(.3)
        self.assertTrue(self.control_observation()["planner"]["paused"], "a present threat must keep the goal paused")
        await self.control_update("clear_monster")
        await self.until(lambda: self.control_observation().get("risk", {}).get("near_monster") is False
                         and not self.control_observation()["planner"]["paused"]
                         and self.control_observation()["planner"]["step_id"] > step, seconds=5)
        result = await self.terminal(receipt["seq"], self.binding())
        self.assertEqual((result["status"], result["evidence"]), ("succeeded", "client_simulated"))

    async def test_control_cancel_closes_owned_connection_without_replay(self):
        receipt = await self.running_navigation()
        try:
            result = await self.adapter.cancel(receipt["seq"], **self.binding())
            self.assertIn(result["status"], {"cancelled", "unknown"})
            self.assertEqual(result["evidence"], "none")
        except direct.AdapterError as error:
            self.assertEqual(str(error), "outcome_unknown")
        await self.until(lambda: self.proc.returncode is not None, seconds=4)
        self.assertFalse(self.adapter.snapshot()["connected"])
        self.assertIsNone(self.adapter.snapshot()["observation"])
        with self.assertRaises(direct.AdapterError):
            await self.adapter.navigate(10, 1, 8)
        self.assertEqual(self.adapter.pending_count, 0)

    async def test_control_off_closes_owned_connection_and_disables_new_commands(self):
        await self.running_navigation()
        await self.adapter.off()
        await self.until(lambda: self.proc.returncode is not None, seconds=4)
        self.assertTrue(self.adapter.snapshot()["resync_required"])
        self.assertIsNone(self.adapter.snapshot()["observation"])
        with self.assertRaises(direct.AdapterError): await self.adapter.look(0, 0)
        with self.assertRaisesRegex(direct.AdapterError, "already_started_or_closed"): await self.adapter.start()

    async def test_advanced_jump_lands_without_rewriting_observed_position(self):
        original = self.control_observation()["self"]["position"]
        self.assertEqual(self.adapter.snapshot()["capabilities"], ["attack", "jump", "look", "navigate"])
        receipt = await self.adapter.jump(run_ms=5000)
        await self.until(lambda: (self.control_observation().get("motion") or {}).get("y", 1) > 1.5)
        self.assertEqual(self.control_observation()["self"]["position"], original)
        result = await self.terminal(receipt["seq"], self.binding())
        self.assertEqual((result["status"], result["evidence"]), ("succeeded", "client_simulated"))
        await self.until(lambda: abs((self.control_observation().get("motion") or {}).get("y", 0) - 1) < 1e-7)

    async def test_advanced_slab_uses_half_height_target(self):
        await self.control_update("slab")
        receipt = await self.adapter.navigate(9, 1.5, 8, run_ms=8000)
        result = await self.terminal(receipt["seq"], self.binding())
        self.assertEqual((result["status"], result["evidence"]), ("succeeded", "client_simulated"))
        await self.until(lambda: (self.control_observation().get("motion") or {}).get("y") == 1.5)

    async def test_advanced_stairs_reach_upper_surface(self):
        await self.control_update("stairs")
        receipt = await self.adapter.navigate(9, 2, 8, run_ms=8000)
        result = await self.terminal(receipt["seq"], self.binding())
        self.assertEqual((result["status"], result["evidence"]), ("succeeded", "client_simulated"))
        await self.until(lambda: (self.control_observation().get("motion") or {}).get("y") == 2)

    async def test_player_water_arrival_maintains_then_off_closes(self):
        await self.test_advanced_water_arrival_maintains_then_off_closes()

    async def test_advanced_water_arrival_maintains_then_off_closes(self):
        await self.control_update("water")
        await self.water_arrival_after_setup()

    async def test_player_partial_tags_preserve_water_arrival(self):
        await self.control_update("water")
        await self.control_update("player_tags_partial")
        await self.water_arrival_after_setup()

    async def water_arrival_after_setup(self):
        receipt = await self.adapter.navigate(9, 2, 8, run_ms=10000)
        result = await self.terminal(receipt["seq"], self.binding())
        self.assertEqual((result["status"], result["evidence"]), ("succeeded", "client_simulated"))
        await self.until(lambda: self.control_observation().get("locomotion", {}).get("maintenance") is True)
        before = self.control_observation()
        self.assertEqual(before["locomotion"]["medium"], "water")
        await asyncio.sleep(.4)
        after = self.control_observation()
        self.assertGreater(after["seq"], before["seq"])
        self.assertLessEqual(abs(after["motion"]["y"] - 2), .2)
        self.assertEqual(after["self"]["position"]["y"], 1.)
        await self.adapter.off()
        await self.until(lambda: self.proc.returncode is not None, seconds=4)
        self.assertIsNone(self.adapter.snapshot()["observation"])

    async def test_advanced_attack_requires_exact_target_and_observed_death(self):
        moved = await self.adapter.navigate(9, 1, 8, run_ms=5000)
        self.assertEqual((await self.terminal(moved["seq"], self.binding()))["status"], "succeeded")
        await self.control_update("empty_hands")
        await self.control_update("target")
        await self.until(lambda: len(self.control_observation().get("targets", [])) == 1)
        target = self.control_observation()["targets"][0]
        receipt = await self.adapter.attack(target["entity_id"], target["spawn_epoch"], run_ms=5000)
        await self.until(lambda: self.control_observation().get("combat", {}).get("attacks_sent", 0) >= 2)
        combat = self.control_observation()["combat"]
        self.assertEqual(combat["evidence"], "none")
        self.assertNotEqual(combat["status"], "succeeded")
        await self.control_update("target_dead")
        result = await self.terminal(receipt["seq"], self.binding())
        self.assertEqual((result["status"], result["code"], result["evidence"]),
                         ("succeeded", "target_defeated_observed", "server_observed"))

    async def test_advanced_target_disappearance_is_not_death(self):
        await self.control_update("empty_hands")
        await self.control_update("target")
        await self.until(lambda: len(self.control_observation().get("targets", [])) == 1)
        target = self.control_observation()["targets"][0]
        receipt = await self.adapter.attack(target["entity_id"], target["spawn_epoch"], run_ms=5000)
        await self.until(lambda: self.control_observation().get("combat", {}).get("attacks_sent", 0) >= 1)
        await self.control_update("target_remove")
        result = await self.terminal(receipt["seq"], self.binding())
        self.assertEqual((result["status"], result["code"], result["evidence"]), ("failed", "target_lost", "none"))

    async def test_actual_java_command_result_and_bounded_rtt(self):
        times = []
        for i in range(25):
            began = time.perf_counter()
            receipt = await self.adapter.command("probe")
            self.assertIn(receipt["status"], {"accepted", "running", "succeeded"})
            result = await self.terminal(receipt["seq"], self.binding())
            self.assertEqual(result["status"], "succeeded")
            if i >= 5:
                times.append((time.perf_counter() - began) * 1000)
        ordered = sorted(times)
        print("SYNTHETIC_COMMAND_TERMINAL_MS " + json.dumps({"n": len(times), "median": round(statistics.median(times), 3),
                                                            "p95": round(ordered[18], 3), "max": round(max(times), 3)}))

    async def test_running_cancel_is_confirmed_and_query_non_consuming(self):
        receipt = await self.adapter.command("hold", run_ms=5000)
        binding = self.binding()
        began = time.perf_counter()
        result = await self.adapter.cancel(receipt["seq"], **binding)
        self.assertEqual(result["status"], "cancelled")
        print("SYNTHETIC_CANCEL_CONFIRMED_MS " + str(round((time.perf_counter() - began) * 1000, 3)))
        for _ in range(2):
            result = await self.adapter.query(receipt["seq"], **binding)
            self.assertEqual(result["status"], "cancelled")

    async def test_disconnect_reconnect_keeps_sequence_without_replay(self):
        receipt = await self.adapter.command("hold", run_ms=10000)
        old = self.adapter.snapshot()
        await self.control("disconnect")
        await self.until(lambda: not self.adapter.snapshot()["connected"])
        await self.control("reconnect")
        await self.adapter.wait_connected(5)
        new = self.adapter.snapshot()
        self.assertEqual(new["session"], old["session"])
        self.assertEqual(new["next_seq"], old["next_seq"])
        result = await self.adapter.query(receipt["seq"], **self.binding())
        self.assertIn(result["status"], {"cancelled", "unknown"})
        next_result = await self.adapter.command("probe")
        self.assertEqual(next_result["seq"], receipt["seq"] + 1)

    async def test_world_generation_invalidates_old_cancel(self):
        command = await self.adapter.command("hold", run_ms=10000)
        old = self.binding()
        await self.control("world:w2")
        await self.until(lambda: self.adapter.snapshot()["world"] == "w2")
        with self.assertRaisesRegex(direct.AdapterError, "stale_binding"):
            await self.adapter.cancel(1, **old)
        self.assertEqual(self.adapter.snapshot()["receipts"], [])
        previous = await self.adapter.query(command["seq"], **self.binding())
        self.assertEqual(previous["status"], "unknown")
        result = await self.adapter.command("probe")
        self.assertNotEqual(result["world"], old["world"])

    async def test_tick_pause_cannot_turn_cancel_into_success(self):
        receipt = await self.adapter.command("hold", run_ms=10000)
        await self.control("pause")
        await asyncio.sleep(0.08)
        with self.assertRaisesRegex(direct.AdapterError, "outcome_unknown"):
            await self.adapter.cancel(receipt["seq"], **self.binding(), timeout=0.15)
        await self.control("resume")
        result = await self.terminal(receipt["seq"], self.binding())
        self.assertEqual(result["status"], "cancelled")

    async def test_real_fragmented_message_and_incomplete_fragment_timeout(self):
        wire = json.dumps({"v": 1, "type": "heartbeat", **self.binding()}, separators=(",", ":")).encode("ascii")
        split = len(wire) // 2
        self.assertLess(max(split, len(wire) - split), 126)
        # Test-only RFC6455 server frames; application API intentionally has no raw writer.
        async with self.adapter._send_lock:
            transport = self.adapter._ws._writer.transport
            transport.write(bytes([0x01, split]) + wire[:split])
            transport.write(bytes([0x80, len(wire) - split]) + wire[split:])
        result = await self.adapter.command("probe")
        self.assertEqual((await self.terminal(result["seq"], self.binding()))["status"], "succeeded")
        # Prevent another data frame from ending the test through an RFC framing error.
        for task in asyncio.all_tasks():
            if getattr(task.get_coro(), "__qualname__", "") == "DirectAdapter._heartbeats":
                task.cancel()
        await self.adapter._send({"v": 1, "type": "heartbeat", **self.binding()})
        await asyncio.sleep(0.05)
        async with self.adapter._send_lock:
            self.adapter._ws._writer.transport.write(b'\x01\x06{"v":1')
        # Java fragment assembly is limited to 2s; owner heartbeat lease is 3s.
        await self.until(lambda: not self.adapter.snapshot()["connected"], seconds=2.6)

    async def test_queue_flood_while_tick_paused_closes_bounded_inbox(self):
        await self.control("pause")
        await asyncio.sleep(0.08)
        binding = self.binding()
        for index in range(70):
            if not self.adapter.snapshot()["connected"]:
                break
            try:
                await self.adapter._send({"v": 1, "type": "query", **binding, "seq": 1,
                                          "request_id": f"flood-{index}"})
            except (direct.AdapterError, ConnectionError):
                break
        await self.until(lambda: not self.adapter.snapshot()["connected"])
        await self.control("resume")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--java-home", required=True)
    parser.add_argument("--gson", required=True)
    OPTIONS, remaining = parser.parse_known_args()
    attempt_spec = importlib.util.spec_from_file_location("_evelyn_test_attempt_capture", ROOT / "tools/run_test_attempt.py")
    attempt_capture = importlib.util.module_from_spec(attempt_spec)
    attempt_spec.loader.exec_module(attempt_capture)
    attempt_capture.install_unittest_capture()
    unittest.main(argv=[sys.argv[0], *remaining])
