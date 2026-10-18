"""Independent official menu facts and fail-closed comparison; no game or sockets."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parents[1] / "minecraft_direct"
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location(
    "menu_oracle_direct", ROOT / "evelyn_core/runtime/evelyn_core/minecraft_direct_adapter.py")
direct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(direct)


def inventory(row, count):
    empty = {"state": "empty", "item_id": None, "count": 0, "patch_known": True}
    return {"revision": 1, "known": True, "container_id": 5, "state_id": 3,
            "menu_type_id": row["id"], "slot_count": row["slot_count"], "page_start": 0,
            "slots": [dict(index=i, **empty) for i in range(min(16, row["slot_count"]))],
            "carried": dict(empty), "selected": 0, "properties": [None] * count,
            "source": "server_observed"}


class MenuPropertyOracleTests(unittest.TestCase):
    def gate(self):
        path = HERE / "menu_property_oracle.py"
        self.assertTrue(path.is_file(), "durable official menu oracle gate is missing")
        spec = importlib.util.spec_from_file_location("menu_property_oracle", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_original_swapped_values_are_rejected(self):
        gate = self.gate()
        oracle = gate.load_oracle()
        observations = [{"menu_type_id": row["id"],
                         "properties": [None] * row["property_count"]}
                        for row in oracle["menus"]]
        observations[21]["properties"] = []
        observations[23]["properties"] = [None]
        with self.assertRaisesRegex(AssertionError, "21.*smithing.*expected 1.*observed 0"):
            gate.check_observations(observations, oracle)

    def test_all_25_real_python_shapes_accept_only_the_official_count(self):
        for row in self.gate().load_oracle()["menus"]:
            with self.subTest(id=row["id"], name=row["name"]):
                count = row["property_count"]
                direct._inventory_observation(inventory(row, count))
                for wrong_count in {count + 1, max(0, count - 1)} - {count}:
                    with self.assertRaises(direct.AdapterError):
                        direct._inventory_observation(inventory(row, wrong_count))

    def test_observation_coverage_and_counts_cannot_silently_shrink(self):
        gate = self.gate()
        oracle = gate.load_oracle()
        good = [inventory(row, row["property_count"]) for row in oracle["menus"]]
        gate.check_observations(good, oracle)
        gate.check_observations(list(reversed(good)), oracle)
        cases = {
            "truncated": good[:-1], "missing_middle": good[:11] + good[12:],
            "duplicate": good[:-1] + [good[0]], "extra": good + [good[0]],
        }
        for label, rows in cases.items():
            with self.subTest(case=label), self.assertRaises(AssertionError):
                gate.check_observations(rows, oracle)
        for menu_id in (0, 7, 11, 21, 23, 24):
            wrong = copy.deepcopy(good)
            wrong[menu_id]["properties"].append(None)
            with self.subTest(menu_id=menu_id), self.assertRaisesRegex(AssertionError, "property count"):
                gate.check_observations(wrong, oracle)
        for bad_id in (True, -1, 25, "24"):
            wrong = copy.deepcopy(good)
            wrong[24]["menu_type_id"] = bad_id
            with self.subTest(id=bad_id), self.assertRaises(AssertionError):
                gate.check_observations(wrong, oracle)

    def test_oracle_requires_pinned_version_ids_names_and_provenance(self):
        gate = self.gate()
        original = gate.load_oracle()
        mutations = {
            "missing": lambda d: d["menus"].pop(),
            "extra": lambda d: d["menus"].append(copy.deepcopy(d["menus"][0])),
            "duplicate_id": lambda d: d["menus"][24].update(id=0),
            "unknown_id": lambda d: d["menus"][24].update(id=25),
            "bool_id": lambda d: d["menus"][0].update(id=False),
            "wrong_name": lambda d: d["menus"][24].update(name="minecraft:invented"),
            "duplicate_name": lambda d: d["menus"][24].update(name=d["menus"][0]["name"]),
            "wrong_version": lambda d: d.update(minecraft_version="26.4"),
            "missing_provenance": lambda d: d.pop("provenance"),
            "missing_source_pin": lambda d: d["provenance"]["bytecode"].pop("sha256"),
            "bad_count": lambda d: d["menus"][0].update(property_count=True),
            "wrong_official_count": lambda d: d["menus"][21].update(property_count=0),
            "wrong_official_slot_count": lambda d: d["menus"][21].update(slot_count=39),
        }
        with tempfile.TemporaryDirectory(prefix="evelyn-menu-oracle-") as temp:
            path = Path(temp) / "oracle.json"
            for label, mutate in mutations.items():
                changed = copy.deepcopy(original)
                mutate(changed)
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.subTest(case=label), self.assertRaises(AssertionError):
                    gate.load_oracle(path)

    def test_missing_or_duplicate_java_report_fails_closed(self):
        gate = self.gate()
        rows = [inventory(row, row["property_count"]) for row in gate.load_oracle()["menus"]]
        report = "MENU_PROPERTY_OBSERVATIONS " + json.dumps(rows)
        gate.check_java_output("MenuPropertiesTest PASS 569\n" + report, direct._inventory_observation)
        for output in ("MenuPropertiesTest PASS 569", report + "\n" + report):
            with self.subTest(output_count=output.count("MENU_PROPERTY_OBSERVATIONS")):
                with self.assertRaisesRegex(AssertionError, "exactly one"):
                    gate.check_java_output(output, direct._inventory_observation)


if __name__ == "__main__":
    unittest.main()
