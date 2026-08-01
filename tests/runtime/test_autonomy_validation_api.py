from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server  # noqa: E402
from evelyn_core.autonomy_validation import SUITE_ID  # noqa: E402
from evelyn_core.control_page_http import CONTROL_PAGE_CSRF_HEADER  # noqa: E402


SESSION_ID = "autonomy-p0-20260801-000000-1234567890"
STEP_ID = "01-explicit-grant"


def session(*, state: str = "preflight") -> dict:
    return {
        "schema": "autonomy_validation.session.v1",
        "sessionId": SESSION_ID,
        "suite": SUITE_ID,
        "state": state,
        "currentStep": {"id": STEP_ID, "attempt": 1},
        "attempt": 1,
        "capabilities": {},
        "summary": {},
        "warnings": [],
        "dryRun": True,
    }


class AutonomyValidationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = Mock()
        self.manager.snapshot.return_value = session()
        self.manager.start.return_value = {"ok": True, "session": session()}
        self.manager.confirm.return_value = {
            "ok": True,
            "session": session(state="running"),
        }
        self.manager.retry.return_value = {
            "ok": True,
            "session": session(state="running"),
        }
        self.manager.abort.return_value = {
            "ok": True,
            "session": session(state="aborted"),
        }
        self.manager_patch = patch.object(
            control_page_server,
            "get_autonomy_validation_manager",
            return_value=self.manager,
        )
        self.manager_patch.start()

        self.external_mocks = {
            "proxy_json": AsyncMock(side_effect=AssertionError("proxy_json called")),
            "proxy_raw": AsyncMock(side_effect=AssertionError("proxy_raw called")),
            "mic_control": AsyncMock(
                side_effect=AssertionError("local mic control called")
            ),
            "runtime_health": AsyncMock(
                side_effect=AssertionError("runtime health called")
            ),
            "raw_runtime_health": AsyncMock(
                side_effect=AssertionError("raw runtime health called")
            ),
            "repair_plan": Mock(side_effect=AssertionError("repair plan called")),
            "repair_apply": Mock(side_effect=AssertionError("repair apply called")),
            "process": Mock(side_effect=AssertionError("process launch called")),
        }
        self.external_patches = [
            patch.object(
                control_page_server,
                "proxy_json",
                new=self.external_mocks["proxy_json"],
            ),
            patch.object(
                control_page_server,
                "proxy_raw",
                new=self.external_mocks["proxy_raw"],
            ),
            patch.object(
                control_page_server,
                "request_local_bridge_mic_control",
                new=self.external_mocks["mic_control"],
            ),
            patch.object(
                control_page_server,
                "cached_runtime_health",
                new=self.external_mocks["runtime_health"],
            ),
            patch.object(
                control_page_server.CONTROL_PAGE_RUNTIME_HEALTH_CACHE,
                "get",
                new=self.external_mocks["raw_runtime_health"],
            ),
            patch.object(
                control_page_server,
                "build_runtime_repair_plan",
                new=self.external_mocks["repair_plan"],
            ),
            patch.object(
                control_page_server,
                "execute_runtime_repair_plan",
                new=self.external_mocks["repair_apply"],
            ),
            patch.object(
                control_page_server.subprocess,
                "Popen",
                new=self.external_mocks["process"],
            ),
        ]
        for active_patch in self.external_patches:
            active_patch.start()

        app = control_page_server.create_app()
        app.cleanup_ctx.clear()
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = str(self.client.make_url("/")).rstrip("/")
        response = await self.client.get(
            "/api/control-page/session",
            headers={"Origin": self.origin},
        )
        self.csrf = (await response.json())["csrfToken"]

    async def asyncTearDown(self):
        await self.client.close()
        for active_patch in reversed(self.external_patches):
            active_patch.stop()
        self.manager_patch.stop()

    def headers(self) -> dict[str, str]:
        return {
            "Origin": self.origin,
            CONTROL_PAGE_CSRF_HEADER: self.csrf,
        }

    @staticmethod
    def valid_payload(suffix: str) -> dict:
        if suffix == "start":
            return {"suite": SUITE_ID, "guildId": "7", "dryRun": True}
        if suffix == "confirm":
            return {
                "sessionId": SESSION_ID,
                "stepId": STEP_ID,
                "attempt": 1,
                "userConfirmed": True,
            }
        if suffix == "retry":
            return {"sessionId": SESSION_ID, "stepId": STEP_ID, "attempt": 1}
        return {"sessionId": SESSION_ID}

    async def test_routes_delegate_only_sanitized_dry_run_arguments(self):
        state_response = await self.client.get(
            "/api/control-page/autonomy-validation",
            headers={"Origin": self.origin},
        )
        self.assertEqual(state_response.status, 200)
        self.assertEqual((await state_response.json())["session"]["suite"], SUITE_ID)

        expected_statuses = {
            "start": 201,
            "confirm": 200,
            "retry": 200,
            "abort": 200,
        }
        for suffix, expected_status in expected_statuses.items():
            response = await self.client.post(
                f"/api/control-page/autonomy-validation/{suffix}",
                headers=self.headers(),
                json=self.valid_payload(suffix),
            )
            self.assertEqual(response.status, expected_status, await response.text())

        self.manager.snapshot.assert_called_once_with()
        self.manager.start.assert_called_once_with(
            suite=SUITE_ID,
            guild_id=7,
            dry_run=True,
        )
        self.manager.confirm.assert_called_once_with(
            session_id=SESSION_ID,
            step_id=STEP_ID,
            attempt=1,
            acknowledged=True,
        )
        self.manager.retry.assert_called_once_with(
            session_id=SESSION_ID,
            step_id=STEP_ID,
            attempt=1,
        )
        self.manager.abort.assert_called_once_with(session_id=SESSION_ID)
        for external in self.external_mocks.values():
            external.assert_not_called()

    async def test_every_mutating_route_requires_csrf_and_json(self):
        for suffix in ("start", "confirm", "retry", "abort"):
            with self.subTest(route=suffix, boundary="csrf"):
                response = await self.client.post(
                    f"/api/control-page/autonomy-validation/{suffix}",
                    headers={"Origin": self.origin},
                    json=self.valid_payload(suffix),
                )
                self.assertEqual(response.status, 403)
                self.assertEqual(
                    (await response.json())["error"],
                    "csrf_token_required",
                )
            with self.subTest(route=suffix, boundary="content-type"):
                response = await self.client.post(
                    f"/api/control-page/autonomy-validation/{suffix}",
                    headers=self.headers(),
                    data=json.dumps(self.valid_payload(suffix)),
                )
                self.assertEqual(response.status, 415)
                self.assertEqual(
                    (await response.json())["error"],
                    "json_content_type_required",
                )

        self.manager.start.assert_not_called()
        self.manager.confirm.assert_not_called()
        self.manager.retry.assert_not_called()
        self.manager.abort.assert_not_called()

    async def test_options_are_non_mutating_and_cors_enabled(self):
        for suffix in ("start", "confirm", "retry", "abort"):
            with self.subTest(route=suffix):
                response = await self.client.options(
                    f"/api/control-page/autonomy-validation/{suffix}",
                    headers={"Origin": self.origin},
                )
                self.assertEqual(response.status, 204)
                self.assertIn(
                    "POST",
                    response.headers["Access-Control-Allow-Methods"],
                )
        self.manager.start.assert_not_called()
        self.manager.confirm.assert_not_called()
        self.manager.retry.assert_not_called()
        self.manager.abort.assert_not_called()

    async def test_unknown_and_dangerous_fields_are_rejected_without_echo(self):
        dangerous_fields = (
            "command",
            "argv",
            "workingDirectory",
            "goal",
            "grantId",
            "leaseId",
        )
        for suffix in ("start", "confirm", "retry", "abort"):
            for field in dangerous_fields:
                with self.subTest(route=suffix, field=field):
                    payload = self.valid_payload(suffix)
                    payload[field] = "sensitive-value"
                    response = await self.client.post(
                        f"/api/control-page/autonomy-validation/{suffix}",
                        headers=self.headers(),
                        json=payload,
                    )
                    body = await response.json()
                    self.assertEqual(response.status, 400)
                    self.assertEqual(body, {"ok": False, "error": "invalid_request_fields"})
                    self.assertNotIn(field, json.dumps(body))
                    self.assertNotIn("sensitive-value", json.dumps(body))

        self.manager.start.assert_not_called()
        self.manager.confirm.assert_not_called()
        self.manager.retry.assert_not_called()
        self.manager.abort.assert_not_called()

    async def test_start_is_exact_suite_positive_integer_and_dry_run_only(self):
        response = await self.client.post(
            "/api/control-page/autonomy-validation/start",
            headers=self.headers(),
            json={"suite": SUITE_ID, "guildId": 7, "dryRun": False},
        )
        body = await response.json()
        self.assertEqual(response.status, 409)
        self.assertEqual(body["error"], "autonomy_execution_not_enabled")
        self.assertTrue(body["dryRunOnly"])

        for dry_run in (None, 1, 0, "true", {}, []):
            with self.subTest(dryRun=dry_run):
                response = await self.client.post(
                    "/api/control-page/autonomy-validation/start",
                    headers=self.headers(),
                    json={"suite": SUITE_ID, "guildId": 7, "dryRun": dry_run},
                )
                self.assertEqual(response.status, 400)
                self.assertEqual((await response.json())["error"], "dry_run_required")

        response = await self.client.post(
            "/api/control-page/autonomy-validation/start",
            headers=self.headers(),
            json={"suite": "autonomy-p0.invalid", "guildId": 7, "dryRun": True},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["error"], "unsupported_suite")

        for guild_id in (
            None,
            True,
            False,
            0,
            -1,
            7.0,
            "0",
            "07",
            "+7",
            " 7",
            "18446744073709551616",
        ):
            with self.subTest(guildId=guild_id):
                response = await self.client.post(
                    "/api/control-page/autonomy-validation/start",
                    headers=self.headers(),
                    json={"suite": SUITE_ID, "guildId": guild_id, "dryRun": True},
                )
                self.assertEqual(response.status, 400)
                self.assertEqual(
                    (await response.json())["error"],
                    "guild_id_positive_required",
                )
        self.manager.start.assert_not_called()

    async def test_start_preserves_full_width_discord_guild_id(self):
        guild_id = "1234567890123456789"

        response = await self.client.post(
            "/api/control-page/autonomy-validation/start",
            headers=self.headers(),
            json={"suite": SUITE_ID, "guildId": guild_id, "dryRun": True},
        )

        self.assertEqual(response.status, 201, await response.text())
        self.manager.start.assert_called_once_with(
            suite=SUITE_ID,
            guild_id=1234567890123456789,
            dry_run=True,
        )

    async def test_confirmation_and_revision_fields_are_exact(self):
        for confirmed in (None, False, 1, "true", {}, []):
            with self.subTest(userConfirmed=confirmed):
                payload = self.valid_payload("confirm")
                payload["userConfirmed"] = confirmed
                response = await self.client.post(
                    "/api/control-page/autonomy-validation/confirm",
                    headers=self.headers(),
                    json=payload,
                )
                self.assertEqual(response.status, 400)
                self.assertEqual(
                    (await response.json())["error"],
                    "user_confirmation_required",
                )

        for suffix in ("confirm", "retry"):
            for attempt in (None, True, False, 0, -1, 1.0, "1"):
                with self.subTest(route=suffix, attempt=attempt):
                    payload = self.valid_payload(suffix)
                    payload["attempt"] = attempt
                    response = await self.client.post(
                        f"/api/control-page/autonomy-validation/{suffix}",
                        headers=self.headers(),
                        json=payload,
                    )
                    self.assertEqual(response.status, 400)
                    self.assertEqual(
                        (await response.json())["error"],
                        "attempt_positive_required",
                    )
        self.manager.confirm.assert_not_called()
        self.manager.retry.assert_not_called()

    async def test_non_object_invalid_json_and_state_conflict_fail_closed(self):
        response = await self.client.post(
            "/api/control-page/autonomy-validation/abort",
            headers=self.headers(),
            json=[],
        )
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["error"], "json_object_required")

        response = await self.client.post(
            "/api/control-page/autonomy-validation/abort",
            headers=self.headers(),
            data="{",
        )
        self.assertEqual(response.status, 415)

        response = await self.client.post(
            "/api/control-page/autonomy-validation/abort",
            headers={**self.headers(), "Content-Type": "application/json"},
            data="{",
        )
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())["error"], "invalid_json")

        self.manager.start.return_value = {
            "ok": False,
            "error": "validation_session_active",
            "session": session(state="running"),
        }
        response = await self.client.post(
            "/api/control-page/autonomy-validation/start",
            headers=self.headers(),
            json=self.valid_payload("start"),
        )
        self.assertEqual(response.status, 409)
        self.assertEqual((await response.json())["error"], "validation_session_active")


if __name__ == "__main__":
    unittest.main()
