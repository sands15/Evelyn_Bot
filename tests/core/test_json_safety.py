import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.json_safety import safe_json_dumps, safe_json_value  # noqa: E402


class JsonSafetyTests(unittest.TestCase):
    def test_dumps_replaces_invalid_surrogates(self) -> None:
        payload = {"speaker": "bad\udcffname", "text": "이블린"}

        encoded = safe_json_dumps(payload, ensure_ascii=False)
        decoded = json.loads(encoded)

        self.assertEqual(decoded["speaker"], "bad?name")
        self.assertEqual(decoded["text"], "이블린")

    def test_value_normalizes_non_json_primitives(self) -> None:
        payload = {
            "nan": math.nan,
            "inf": math.inf,
            "np_float": np.float32(1.25),
            "np_int": np.int64(7),
            "bytes": b"\xed\xa0\x80ok",
            "set": {"a", "b"},
        }

        normalized = safe_json_value(payload)
        encoded = safe_json_dumps(payload, ensure_ascii=False, sort_keys=True)
        decoded = json.loads(encoded)

        self.assertIsNone(normalized["nan"])
        self.assertIsNone(decoded["inf"])
        self.assertAlmostEqual(decoded["np_float"], 1.25)
        self.assertEqual(decoded["np_int"], 7)
        self.assertIn("ok", decoded["bytes"])
        self.assertEqual(sorted(decoded["set"]), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
