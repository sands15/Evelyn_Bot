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

    def test_tts_health_probe_requires_exact_omnivoice_model(self) -> None:
        manifest = load_service_manifest(force=True)
        service = get_service(manifest, "tts")

        self.assertIsNotNone(service)
        assert service is not None
        health = next(check for check in service.checks if check.kind == "http")
        self.assertEqual(
            health.expect_json,
            {
                "status": "healthy",
                "ready": True,
                "model_loaded": True,
                "model_id": "k2-fsa/OmniVoice",
                "model_revision": "c5fdb5ccb189668d56333f77ba2629f4cd7535f4",
                "runtime_revision": "omnivoice-0.1.5",
                "flashinfer_revision": "28bc0889d92110491d726a9c79f26a895db5a074",
                "inference_backend": "flashinfer_cuda_graph",
                "flashinfer_python_version": "0.6.15.post1",
                "flashinfer_jit_cache_version": "0.6.15.post1+cu129",
                "torch_version": "2.8.0+cu129",
                "torch_cuda_version": "12.9",
                "flashinfer_jit_disabled": True,
                "flashinfer_cuda_graph_buckets": [2.0, 4.0, 8.0],
                "max_concurrent": 1,
                "num_step": 12,
            },
        )
        assert health.expect_json is not None
        self.assertIs(type(health.expect_json["flashinfer_jit_disabled"]), bool)
        self.assertIs(type(health.expect_json["max_concurrent"]), int)
        self.assertIs(type(health.expect_json["num_step"]), int)
        self.assertTrue(
            all(
                type(value) is float
                for value in health.expect_json["flashinfer_cuda_graph_buckets"]
            )
        )
        self.assertEqual(service.launcher, "../start_tts.bat")

    def test_codex_health_requires_the_verified_tool_boundary(self) -> None:
        manifest = load_service_manifest(force=True)
        service = get_service(manifest, "codex_gateway")

        self.assertIsNotNone(service)
        assert service is not None
        health = next(check for check in service.checks if check.kind == "http")
        self.assertEqual(
            health.expect_json,
            {
                "backendReady": True,
                "isolatedRuntime": True,
                "toolAccessVerified": True,
            },
        )

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
