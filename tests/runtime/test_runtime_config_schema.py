from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_config_schema import (  # noqa: E402
    RUNTIME_CONFIG_SCHEMA,
    SettingSpec,
    VISION_SERVICE_SETTINGS,
    load_runtime_settings,
)
from evelyn_core.vision_remote_model_lock import FALCON_OCR_REVISION  # noqa: E402


class RuntimeConfigSchemaTests(unittest.TestCase):
    def test_typed_values_are_parsed_and_bounded(self) -> None:
        settings = load_runtime_settings(
            "unit",
            (
                SettingSpec("ENABLED", kind="bool", default=False),
                SettingSpec(
                    "PORT",
                    kind="int",
                    default=1000,
                    minimum=1,
                    maximum=65535,
                ),
                SettingSpec("URL", kind="url", default="http://localhost"),
            ),
            environ={
                "ENABLED": "yes",
                "PORT": "8799",
                "URL": "http://127.0.0.1:8799/",
            },
        )

        self.assertTrue(settings["ENABLED"])
        self.assertEqual(settings["PORT"], 8799)
        self.assertEqual(settings["URL"], "http://127.0.0.1:8799")
        self.assertEqual(settings.warnings, ())

    def test_invalid_values_fall_back_without_exposing_raw_value(self) -> None:
        settings = load_runtime_settings(
            "unit",
            (
                SettingSpec(
                    "PORT",
                    kind="int",
                    default=8799,
                    minimum=1,
                    maximum=65535,
                ),
                SettingSpec("TOKEN", default="", secret=True),
            ),
            environ={
                "PORT": "C:\\private\\invalid",
                "TOKEN": "top-secret-value",
            },
        )

        self.assertEqual(settings["PORT"], 8799)
        summary = settings.public_summary()
        self.assertEqual(summary["schema"], RUNTIME_CONFIG_SCHEMA)
        self.assertTrue(summary["secrets"]["TOKEN"])
        serialized = json.dumps(summary)
        self.assertNotIn("private", serialized)
        self.assertNotIn("top-secret-value", serialized)

    def test_alias_is_reported_without_value(self) -> None:
        settings = load_runtime_settings(
            "unit",
            (
                SettingSpec("CURRENT", default="default", aliases=("OLD",)),
            ),
            environ={"OLD": "selected"},
        )

        self.assertEqual(settings["CURRENT"], "selected")
        self.assertEqual(
            settings.public_summary()["warnings"],
            [{"field": "CURRENT", "code": "deprecated_alias"}],
        )

    def test_vision_ocr_defaults_to_exact_offline_snapshot(self) -> None:
        settings = load_runtime_settings(
            "vision",
            VISION_SERVICE_SETTINGS,
            environ={},
        )

        self.assertEqual(settings["VISION_OCR_REVISION"], FALCON_OCR_REVISION)
        self.assertIs(settings["VISION_OCR_LOCAL_FILES_ONLY"], True)


if __name__ == "__main__":
    unittest.main()
