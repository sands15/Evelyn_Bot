from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())


class ActionWaitGuardContractTests(unittest.TestCase):
    def test_mineflayer_bridge_exposes_wait_guard_and_parched_detection(self) -> None:
        source = (
            REPO_ROOT / "third_party" / "Voyager" / "voyager" / "env" / "mineflayer" / "index.js"
        ).read_text(encoding="utf-8")

        self.assertIn('"parched"', source)
        self.assertIn('app.post("/guard"', source)
        self.assertIn('action: "retreat_from_hostile"', source)
        self.assertIn('action: "escape_water"', source)
        self.assertIn("liquidCost = 100", source)
        self.assertIn('strategy: hostileDistance <= 5 ? "emergency_sprint" : "dry_path_retreat"', source)
        self.assertIn("ensureWaitGuardTimer(bot)", source)
        self.assertIn('MINEFLAYER_ALLOW_SERVER_CHEATS || "false"', source)
        self.assertIn('message: "No-op in survival mode"', source)

    def test_python_bridge_and_voyager_toggle_guard_around_action_request(self) -> None:
        bridge_source = (
            REPO_ROOT / "third_party" / "Voyager" / "voyager" / "env" / "bridge.py"
        ).read_text(encoding="utf-8")
        voyager_source = (
            REPO_ROOT / "third_party" / "Voyager" / "voyager" / "voyager.py"
        ).read_text(encoding="utf-8")

        self.assertIn("def set_wait_guard(self, enabled: bool)", bridge_source)
        self.assertIn('f"{self.server}/guard"', bridge_source)
        self.assertIn("self._set_action_wait_guard(True)", voyager_source)
        self.assertIn("finally:\n                self._set_action_wait_guard(False)", voyager_source)
        self.assertIn('events = self.env.step("")', voyager_source)


if __name__ == "__main__":
    unittest.main()
