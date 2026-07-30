from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_services import (  # noqa: E402
    get_service,
    load_service_manifest,
    service_port_map,
    validate_service_manifest,
)


class ServiceManifestTests(unittest.TestCase):
    def test_manifest_keeps_control_page_and_bot_api_ports_separate(self) -> None:
        manifest = load_service_manifest(force=True)
        ports = service_port_map(manifest)

        self.assertEqual(ports["control_page"], 8799)
        self.assertEqual(ports["bot_api"], 8798)
        self.assertNotEqual(ports["control_page"], ports["bot_api"])
        self.assertFalse(validate_service_manifest(manifest))

    def test_manifest_contains_first_phase_services(self) -> None:
        manifest = load_service_manifest(force=True)
        service_ids = {service.id for service in manifest.services}

        self.assertIn("control_page", service_ids)
        self.assertIn("bot_api", service_ids)
        self.assertIn("main_llm", service_ids)
        self.assertIn("router_llm", service_ids)
        self.assertIn("sub_llm", service_ids)
        self.assertIn("tts", service_ids)
        self.assertIn("stt", service_ids)
        self.assertIn("vision", service_ids)
        self.assertIn("minecraft_world_lease", service_ids)

        lease_service = get_service(
            manifest,
            "minecraft_world_lease",
        )
        self.assertIsNotNone(lease_service)
        assert lease_service is not None
        self.assertFalse(lease_service.required)
        self.assertEqual(
            lease_service.checks[0].kind,
            "artifact_json",
        )

        ports = service_port_map(manifest)
        self.assertEqual(ports["stt"], 8892)

    def test_environment_override_is_reflected_in_effective_port(self) -> None:
        original = os.environ.get("CONTROL_PAGE_BOT_API_PORT")
        os.environ["CONTROL_PAGE_BOT_API_PORT"] = "18098"
        try:
            manifest = load_service_manifest(force=True)
            service = get_service(manifest, "bot_api")
        finally:
            if original is None:
                os.environ.pop("CONTROL_PAGE_BOT_API_PORT", None)
            else:
                os.environ["CONTROL_PAGE_BOT_API_PORT"] = original
            load_service_manifest(force=True)

        self.assertIsNotNone(service)
        assert service is not None
        self.assertEqual(service.port, 18098)
        self.assertEqual(service.default_port, 8798)
        self.assertEqual(service.port_env, "CONTROL_PAGE_BOT_API_PORT")

    def test_environment_override_is_reflected_in_effective_host(self) -> None:
        original = os.environ.get("CONTROL_PAGE_BOT_API_HOST")
        os.environ["CONTROL_PAGE_BOT_API_HOST"] = "bot_api"
        try:
            manifest = load_service_manifest(force=True)
            service = get_service(manifest, "bot_api")
        finally:
            if original is None:
                os.environ.pop("CONTROL_PAGE_BOT_API_HOST", None)
            else:
                os.environ["CONTROL_PAGE_BOT_API_HOST"] = original
            load_service_manifest(force=True)

        self.assertIsNotNone(service)
        assert service is not None
        self.assertEqual(service.host, "bot_api")
        self.assertEqual(service.default_host, "127.0.0.1")
        self.assertEqual(service.host_env, "CONTROL_PAGE_BOT_API_HOST")


if __name__ == "__main__":
    unittest.main()
