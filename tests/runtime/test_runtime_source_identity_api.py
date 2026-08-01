from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


REPO_ROOT = next(
    path
    for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server  # noqa: E402
from evelyn_core import fast_control_api  # noqa: E402
from evelyn_core.runtime_source_identity import (  # noqa: E402
    runtime_source_identity,
)


REVISION_A = "a" * 40
REVISION_B = "b" * 40


def container_environment(role: str, revision: str) -> dict[str, str]:
    return {
        "EVELYN_RUNTIME_ROLE": role,
        "EVELYN_IMAGE_SOURCE_REVISION": revision,
        "EVELYN_EXPECTED_SOURCE_REVISION": revision,
    }


def ready_health() -> dict[str, object]:
    legacy = {
        "botReady": True,
        "mainReady": True,
        "routerReady": True,
        "subReady": True,
        "ttsReady": True,
        "sttReady": True,
    }
    return {
        "ok": True,
        "fullyHealthy": True,
        "legacyServices": legacy,
        "services": [
            {"id": service_id, "state": "up", "ready": True}
            for service_id in (
                "control_page",
                "bot_api",
                "main_llm",
                "router_llm",
                "sub_llm",
                "tts",
                "stt",
            )
        ],
        "manifestVersion": "1.1",
    }


class FastControlSourceIdentityApiTests(unittest.TestCase):
    def test_unverified_container_source_closes_state_readiness(self) -> None:
        environment = {"EVELYN_RUNTIME_ROLE": "bot_api"}
        with patch.dict(os.environ, environment, clear=True):
            state = fast_control_api.build_control_state(ready_health())

        self.assertFalse(state["ok"])
        self.assertFalse(state["chat"]["inputEnabled"])
        self.assertFalse(state["runtime"]["services"]["sourceAligned"])
        self.assertEqual(
            state["runtime"]["sourceIdentity"]["state"],
            "unverified",
        )
        source_step = next(
            step
            for step in state["bootProgress"]["steps"]
            if step["key"] == "source_identity"
        )
        self.assertFalse(source_step["done"])
        self.assertFalse(state["bootProgress"]["ready"])

    def test_unverified_container_source_closes_health(self) -> None:
        environment = {"EVELYN_RUNTIME_ROLE": "bot_api"}
        with patch.dict(os.environ, environment, clear=True):
            response = asyncio.run(fast_control_api.health_handler(None))

        payload = json.loads(response.text or "{}")
        self.assertEqual(response.status, 503)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["sourceIdentity"]["state"], "unverified")


class ControlPageSourceIdentityApiTests(unittest.IsolatedAsyncioTestCase):
    def remote_payload(self, revision: str) -> dict[str, object]:
        return {
            "ok": True,
            "runtime": {
                "services": {"botReady": True},
                "sourceIdentity": runtime_source_identity(
                    container_environment("bot_api", revision)
                ),
            },
        }

    def test_container_proxy_requires_exact_remote_revision(self) -> None:
        with patch.dict(
            os.environ,
            container_environment("control_page", REVISION_A),
            clear=True,
        ):
            self.assertTrue(
                control_page_server.bot_source_identity_compatible(
                    self.remote_payload(REVISION_A)
                )
            )
            self.assertFalse(
                control_page_server.bot_source_identity_compatible(
                    self.remote_payload(REVISION_B)
                )
            )
            self.assertFalse(
                control_page_server.bot_source_identity_compatible(
                    {"ok": True, "runtime": {}}
                )
            )

    async def test_mismatched_bot_state_is_returned_as_degraded(self) -> None:
        class Request(dict):
            query_string = ""

        proxied = control_page_server.web.Response(
            status=200,
            text=json.dumps(self.remote_payload(REVISION_B)),
        )
        with patch.dict(
            os.environ,
            container_environment("control_page", REVISION_A),
            clear=True,
        ):
            with patch.object(
                control_page_server,
                "proxy_json",
                new=AsyncMock(return_value=proxied),
            ):
                with patch.object(
                    control_page_server,
                    "cached_runtime_health",
                    new=AsyncMock(return_value=ready_health()),
                ):
                    response = await control_page_server.state_handler(Request())

        payload = json.loads(response.text or "{}")
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["bootProgress"]["ready"])
        self.assertFalse(payload["runtime"]["services"]["botReady"])
        self.assertFalse(payload["runtime"]["services"]["sourceAligned"])
        self.assertEqual(
            payload["runtime"]["controlPlane"]["lastProxyFailure"]["kind"],
            "source_revision_mismatch",
        )
        self.assertIn("source revision", payload["statusText"])

    async def test_health_requires_local_and_bot_source_alignment(self) -> None:
        bot_identity = runtime_source_identity(
            container_environment("bot_api", REVISION_B)
        )
        with patch.dict(
            os.environ,
            container_environment("control_page", REVISION_A),
            clear=True,
        ):
            with patch.object(
                control_page_server,
                "probe_bot_health_identity",
                new=AsyncMock(return_value=(False, bot_identity)),
            ):
                response = await control_page_server.health_handler(None)

        payload = json.loads(response.text or "{}")
        self.assertEqual(response.status, 503)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["botProxyReady"])
        self.assertEqual(payload["sourceIdentity"]["state"], "aligned")


if __name__ == "__main__":
    unittest.main()
