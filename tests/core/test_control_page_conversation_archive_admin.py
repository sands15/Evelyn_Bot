from __future__ import annotations

import hashlib
import hmac
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import control_page_server  # noqa: E402
from evelyn_core.control_page_http import (  # noqa: E402
    CONTROL_PAGE_CSRF_HEADER,
    CONTROL_PAGE_CSRF_TOKEN,
)


COOKIE_NAME = control_page_server.CONVERSATION_ARCHIVE_ADMIN_COOKIE
COOKIE_VALUE = "ArchiveSessionToken_0123456789_abcdefghijk"
SYNTHETIC_BODY = "SYNTHETIC_RECORD_BODY_MARKER"
PROXY_MASTER_KEY = b"synthetic-control-proxy-master-key-0123456789"
BOOTSTRAP_NONCE = "synthetic_bootstrap_nonce_0123456789"


class ControlPageConversationArchiveAdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.seen: list[dict[str, object]] = []
        self.challenge_extra_field = False
        self.challenge_leaks_code = False
        self.challenge_private_error = False
        self.challenge_redirects = False
        self.challenge_sets_cookie = False
        self.login_cookie_is_invalid = False
        self.login_cookie_is_immediately_deleted = False
        self.records_authentication_required = False
        self.records_over_page_limit = False
        self.voice_transition_leaks_body = False
        self.legal_leaks_identifier = False
        self.feedback_workflow_leaks_private_body = False
        self.feedback_nonapproval_preview_leaks_guidance = False

        async def upstream_handler(request: web.Request) -> web.Response:
            try:
                raw_body = await request.read()
                body = json.loads(raw_body) if request.method == "POST" else None
            except Exception:
                raw_body = b""
                body = None
            self.seen.append(
                {
                    "method": request.method,
                    "path": request.path,
                    "body": body,
                    "rawBody": raw_body,
                    "cookie": request.headers.get("Cookie", ""),
                    "internal": request.headers.get(
                        control_page_server.EVELYN_INTERNAL_CONTROL_HEADER,
                        "",
                    ),
                    "timestamp": request.headers.get(
                        control_page_server._CONVERSATION_ARCHIVE_TIMESTAMP_HEADER,
                        "",
                    ),
                    "nonce": request.headers.get(
                        control_page_server._CONVERSATION_ARCHIVE_NONCE_HEADER,
                        "",
                    ),
                    "signature": request.headers.get(
                        control_page_server._CONVERSATION_ARCHIVE_SIGNATURE_HEADER,
                        "",
                    ),
                    "scheme": request.headers.get(
                        control_page_server._CONVERSATION_ARCHIVE_CONTROL_SCHEME_HEADER,
                        "",
                    ),
                    "host": request.headers.get(
                        control_page_server._CONVERSATION_ARCHIVE_CONTROL_HOST_HEADER,
                        "",
                    ),
                    "origin": request.headers.get(
                        control_page_server._CONVERSATION_ARCHIVE_CONTROL_ORIGIN_HEADER,
                        "",
                    ),
                }
            )
            action = request.match_info["action"]
            if action == "challenge":
                if body != {"bootstrapNonce": BOOTSTRAP_NONCE}:
                    return web.json_response(
                        {"ok": False, "error": "archive_request_invalid"},
                        status=400,
                    )
                if self.challenge_redirects:
                    raise web.HTTPFound(
                        location="/internal/conversation-archive/admin/records"
                    )
                if self.challenge_private_error:
                    return web.json_response(
                        {
                            "ok": False,
                            "error": "archive_transport_auth_invalid",
                            "state": "rejected",
                            "retryable": False,
                            "detail": "SYNTHETIC_PRIVATE_ERROR_MARKER",
                        },
                        status=403,
                    )
                payload = {
                    "ok": True,
                    "state": "otp_delivery_pending",
                    "challengeId": "synthetic_challenge_marker_1234",
                }
                if self.challenge_leaks_code:
                    payload["code"] = "Aa1Z"
                if self.challenge_extra_field:
                    payload["detail"] = "SYNTHETIC_UNEXPECTED_SUCCESS_MARKER"
                response = web.json_response(payload)
                if self.challenge_sets_cookie:
                    response.set_cookie(
                        COOKIE_NAME,
                        COOKIE_VALUE,
                        secure=True,
                        httponly=True,
                        samesite="Strict",
                        path="/",
                    )
                return response
            if action == "login":
                response = web.json_response(
                    {"ok": True, "state": "authenticated"}
                )
                response.set_cookie(
                    COOKIE_NAME,
                    COOKIE_VALUE,
                    secure=not self.login_cookie_is_invalid,
                    httponly=True,
                    samesite="Strict",
                    path="/",
                    max_age=(
                        0 if self.login_cookie_is_immediately_deleted else None
                    ),
                )
                response.set_cookie("unrelated", "must-not-cross")
                return response
            if action == "records":
                if self.records_authentication_required:
                    return web.json_response(
                        {
                            "schema": "conversation_archive.admin-public-status.v1",
                            "ok": False,
                            "state": "authentication_required",
                            "retryable": False,
                        },
                        status=403,
                    )
                cursor = str((body or {}).get("cursor") or "")
                record = {
                    "recordId": (
                        "synthetic-record-2"
                        if cursor
                        else "synthetic-record-1"
                    ),
                    "createdAt": "2030-01-01T00:00:00Z",
                    "kind": "synthetic",
                    "ownerName": "Synthetic Owner",
                    "body": (
                        "SYNTHETIC_SECOND_PAGE_MARKER"
                        if cursor
                        else SYNTHETIC_BODY
                    ),
                }
                records = [record]
                if self.records_over_page_limit:
                    records = [
                        {**record, "recordId": f"synthetic-record-{index}"}
                        for index in range(3)
                    ]
                return web.json_response(
                    {
                        "ok": True,
                        "records": records,
                        "nextCursor": (
                            "synthetic_cursor_marker" if not cursor else None
                        ),
                    }
                )
            if action == "participation":
                cursor = str((body or {}).get("cursor") or "")
                return web.json_response(
                    {
                        "ok": True,
                        "intervals": [
                            {
                                "intervalId": (
                                    "synthetic-interval-2"
                                    if cursor
                                    else "synthetic-interval-1"
                                ),
                                "principalId": "synthetic-principal",
                                "ownerName": "Synthetic Owner",
                                "guildId": "223456789012345678",
                                "channelId": "423456789012345678",
                                "kind": "eligible",
                                "startedAt": "2030-01-01T00:00:00Z",
                                "endedAt": "2030-01-01T00:01:00Z",
                            }
                        ],
                        "nextCursor": "a" * 64 if not cursor else None,
                    }
                )
            if action == "voice-state-transitions":
                cursor = str((body or {}).get("cursor") or "")
                transition = {
                    "transitionId": (
                        "synthetic-transition-2"
                        if cursor
                        else "synthetic-transition-1"
                    ),
                    "principalId": "synthetic-principal",
                    "ownerName": "Synthetic Owner",
                    "guildId": "223456789012345678",
                    "channelId": "423456789012345678",
                    "eventAt": "2030-01-01T00:00:00Z",
                    "present": True,
                    "consentCurrent": True,
                    "selfMute": False,
                    "serverMute": False,
                    "selfDeaf": False,
                    "serverDeaf": False,
                    "suppressed": False,
                    "gatewayKnown": True,
                }
                if self.voice_transition_leaks_body:
                    transition["body"] = "must-not-cross"
                return web.json_response(
                    {
                        "ok": True,
                        "transitions": [transition],
                        "nextCursor": "b" * 64 if not cursor else None,
                    }
                )
            if action == "legal-minimal":
                event = {
                    "ownerName": "Synthetic Owner",
                    "occurredAt": "2030-01-01T00:00:00Z",
                }
                if self.legal_leaks_identifier:
                    event["recordId"] = "must-not-cross"
                return web.json_response(
                    {
                        "ok": True,
                        "events": [event],
                        "nextCursor": None,
                    }
                )
            if action == "delete/preview":
                return web.json_response(
                    {
                        "ok": True,
                        "state": "step_up_pending",
                        "previewToken": "synthetic-preview-token",
                        "affectedCount": 1,
                    }
                )
            if action == "delete/apply":
                return web.json_response(
                    {
                        "ok": True,
                        "state": "deleted",
                        "affectedCount": 1,
                        "requestId": "synthetic-request-id",
                    }
                )
            feedback_workflow = {
                "schema": "evelyn.feedback-workflow-public.v1",
                "workflowId": "synthetic-workflow",
                "state": "independent_candidate",
                "category": "answer_quality",
                "route": "task_guidance_review",
                "actionable": True,
                "sourceRecordId": "synthetic-record-1",
                "versionId": "synthetic-version",
                "activeVersionId": "base",
                "deletionStates": [],
                "contentFree": True,
            }
            if self.feedback_workflow_leaks_private_body:
                feedback_workflow["correction"] = "SYNTHETIC_PRIVATE_CORRECTION"
            if action == "feedback/workflows":
                return web.json_response(
                    {
                        "ok": True,
                        "workflows": [feedback_workflow],
                        "activeVersionId": "base",
                    }
                )
            if action in {
                "feedback/capture",
                "feedback/generalize",
                "feedback/evaluate",
                "feedback/approval/apply",
                "feedback/canary",
                "feedback/activate",
            }:
                return web.json_response(
                    {"ok": True, "workflow": feedback_workflow}
                )
            if action in {
                "feedback/approval/preview",
                "feedback/rollback/preview",
                "feedback/revoke/preview",
            }:
                guidance = (
                    "SYNTHETIC_OPERATOR_GUIDANCE"
                    if action == "feedback/approval/preview"
                    else ""
                )
                if (
                    self.feedback_nonapproval_preview_leaks_guidance
                    and action != "feedback/approval/preview"
                ):
                    guidance = "SYNTHETIC_FORBIDDEN_GUIDANCE"
                return web.json_response(
                    {
                        "ok": True,
                        "state": "step_up_pending",
                        "previewToken": "synthetic_feedback_preview",
                        "versionId": "synthetic-version",
                        "guidance": guidance,
                    }
                )
            if action == "feedback/rollback/apply":
                return web.json_response(
                    {
                        "ok": True,
                        "state": "rolled_back",
                        "versionId": "synthetic-version",
                        "activeVersionId": "base",
                    }
                )
            if action == "feedback/failure":
                return web.json_response(
                    {
                        "ok": True,
                        "state": "failure_recorded",
                        "failureId": "synthetic-failure",
                        "versionId": "synthetic-version",
                    }
                )
            if action == "feedback/revoke/apply":
                return web.json_response(
                    {
                        "ok": True,
                        "state": "revoked",
                        "versionIds": ["synthetic-version"],
                        "activeVersionId": "base",
                    }
                )
            if action == "logout":
                response = web.json_response(
                    {"ok": True, "state": "logged_out"}
                )
                response.del_cookie(
                    COOKIE_NAME,
                    secure=True,
                    httponly=True,
                    samesite="Strict",
                    path="/",
                )
                response.set_cookie("unrelated", "must-not-cross")
                return response
            raise web.HTTPNotFound()

        upstream_app = web.Application()
        upstream_app.router.add_route(
            "*",
            "/internal/conversation-archive/admin/{action:.*}",
            upstream_handler,
        )
        self.upstream = TestServer(upstream_app)
        await self.upstream.start_server()

        self.temporary = tempfile.TemporaryDirectory()
        self.proxy_key_path = Path(self.temporary.name) / "proxy.key"
        self.proxy_key_path.write_bytes(PROXY_MASTER_KEY)

        self.patches = [
            patch.object(
                control_page_server,
                "BOT_API_BASE",
                str(self.upstream.make_url("/")).rstrip("/"),
            ),
            patch.object(
                control_page_server,
                "CONVERSATION_ARCHIVE_ENABLED",
                True,
            ),
            patch.object(
                control_page_server,
                "CONVERSATION_ARCHIVE_PROXY_KEY_FILE",
                str(self.proxy_key_path),
            ),
            patch.object(
                control_page_server,
                "_conversation_archive_request_is_secure",
                return_value=True,
            ),
        ]
        for patcher in self.patches:
            patcher.start()

        self.client = TestClient(
            TestServer(
                control_page_server.create_app(
                    manage_voice_capture_consent=False
                )
            )
        )
        await self.client.start_server()
        self.origin = str(self.client.make_url("/")).rstrip("/")

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.upstream.close()
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temporary.cleanup()

    def browser_headers(
        self,
        *,
        cookie: bool = False,
        csrf: bool = False,
        origin: str | None = None,
    ) -> dict[str, str]:
        headers = {"Origin": self.origin if origin is None else origin}
        if csrf:
            headers[CONTROL_PAGE_CSRF_HEADER] = CONTROL_PAGE_CSRF_TOKEN
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = (
                f"unrelated=browser-secret; {COOKIE_NAME}={COOKIE_VALUE}"
            )
        return headers

    async def post(
        self,
        path: str,
        body: dict[str, object],
        *,
        cookie: bool = False,
    ) -> web.Response:
        return await self.client.post(
            path,
            data=json.dumps(body),
            headers=self.browser_headers(cookie=cookie, csrf=True),
        )

    async def test_full_admin_proxy_flow_is_exact_and_cookie_isolated(self) -> None:
        prefix = "/api/control-page/conversation-archive/admin"
        challenge = await self.post(
            f"{prefix}/challenge",
            {"bootstrapNonce": BOOTSTRAP_NONCE},
        )
        self.assertEqual(challenge.status, 200)
        self.assertEqual(
            (await challenge.json())["challengeId"],
            "synthetic_challenge_marker_1234",
        )
        self.assertFalse(challenge.headers.getall("Set-Cookie", []))

        login = await self.post(
            f"{prefix}/login",
            {
                "challengeId": "synthetic_challenge_marker_1234",
                "code": "Aa1Z",
            },
        )
        self.assertEqual(login.status, 200)
        cookies = login.headers.getall("Set-Cookie", [])
        self.assertEqual(len(cookies), 1)
        self.assertIn(f"{COOKIE_NAME}={COOKIE_VALUE}", cookies[0])
        self.assertIn("Secure", cookies[0])
        self.assertIn("HttpOnly", cookies[0])
        self.assertIn("SameSite=Strict", cookies[0])
        self.assertIn("Path=/", cookies[0])
        self.assertNotIn("unrelated", cookies[0])

        records = await self.client.get(
            f"{prefix}/records",
            headers=self.browser_headers(cookie=True),
        )
        self.assertEqual(records.status, 200)
        self.assertEqual((await records.json())["records"][0]["body"], SYNTHETIC_BODY)
        self.assertIn("no-store", records.headers["Cache-Control"])
        second_page = await self.client.get(
            f"{prefix}/records?cursor=synthetic_cursor_marker",
            headers=self.browser_headers(cookie=True),
        )
        self.assertEqual(second_page.status, 200)
        self.assertEqual(
            (await second_page.json())["records"][0]["body"],
            "SYNTHETIC_SECOND_PAGE_MARKER",
        )
        participation = await self.client.get(
            f"{prefix}/participation",
            headers=self.browser_headers(cookie=True),
        )
        self.assertEqual(participation.status, 200)
        participation_payload = await participation.json()
        self.assertEqual(
            participation_payload["intervals"][0]["kind"],
            "eligible",
        )
        participation_cursor = participation_payload["nextCursor"]
        self.assertRegex(participation_cursor, r"^[0-9a-f]{64}$")
        participation_next = await self.client.get(
            f"{prefix}/participation?cursor={participation_cursor}",
            headers=self.browser_headers(cookie=True),
        )
        self.assertEqual(participation_next.status, 200)
        voice_transitions = await self.client.get(
            f"{prefix}/voice-state-transitions",
            headers=self.browser_headers(cookie=True),
        )
        self.assertEqual(voice_transitions.status, 200)
        voice_transitions_payload = await voice_transitions.json()
        self.assertIs(
            voice_transitions_payload["transitions"][0]["gatewayKnown"],
            True,
        )
        voice_transitions_cursor = voice_transitions_payload["nextCursor"]
        self.assertRegex(voice_transitions_cursor, r"^[0-9a-f]{64}$")
        voice_transitions_next = await self.client.get(
            f"{prefix}/voice-state-transitions?cursor={voice_transitions_cursor}",
            headers=self.browser_headers(cookie=True),
        )
        self.assertEqual(voice_transitions_next.status, 200)
        legal = await self.client.get(
            f"{prefix}/legal-minimal",
            headers=self.browser_headers(cookie=True),
        )
        self.assertEqual(legal.status, 200)
        legal_payload = await legal.json()
        self.assertEqual(
            set(legal_payload["events"][0]),
            {"ownerName", "occurredAt"},
        )
        self.assertNotIn("recordId", await legal.text())
        self.assertNotIn("principalId", await legal.text())

        preview_body = {
            "recordIds": ["synthetic-record-1"],
            "startedAt": "2030-01-01T00:00:00Z",
            "endedAt": "2030-01-02T00:00:00Z",
        }
        preview = await self.post(
            f"{prefix}/delete/preview",
            preview_body,
            cookie=True,
        )
        self.assertEqual(preview.status, 200)
        self.assertEqual(
            (await preview.json())["previewToken"],
            "synthetic-preview-token",
        )

        apply_body = {
            "previewToken": "synthetic-preview-token",
            "code": "Z1aB",
        }
        applied = await self.post(
            f"{prefix}/delete/apply",
            apply_body,
            cookie=True,
        )
        self.assertEqual(applied.status, 200)
        self.assertNotIn("requestId", await applied.json())

        logout = await self.post(f"{prefix}/logout", {}, cookie=True)
        self.assertEqual(logout.status, 200)
        logout_cookies = logout.headers.getall("Set-Cookie", [])
        self.assertEqual(len(logout_cookies), 1)
        self.assertIn(f"{COOKIE_NAME}=", logout_cookies[0])
        self.assertIn("Max-Age=0", logout_cookies[0])
        self.assertNotIn("unrelated", logout_cookies[0])

        records_call = next(row for row in self.seen if row["path"].endswith("/records"))
        self.assertEqual(records_call["method"], "POST")
        self.assertEqual(records_call["body"], {})
        self.assertEqual(records_call["cookie"], f"{COOKIE_NAME}={COOKIE_VALUE}")
        self.assertEqual(records_call["internal"], "")
        transport_key = hmac.new(
            PROXY_MASTER_KEY,
            control_page_server._CONVERSATION_ARCHIVE_PROXY_KEY_DOMAIN,
            hashlib.sha256,
        ).digest()

        def assert_transport_signature(call: dict[str, object]) -> None:
            canonical = "\n".join(
                (
                    "control-proxy",
                    str(call["method"]),
                    str(call["path"]),
                    str(call["timestamp"]),
                    str(call["nonce"]),
                    hashlib.sha256(call["rawBody"]).hexdigest(),
                    str(call["scheme"]),
                    str(call["host"]),
                    str(call["origin"]),
                )
            ).encode("utf-8")
            self.assertTrue(
                hmac.compare_digest(
                    str(call["signature"]),
                    hmac.new(
                        transport_key, canonical, hashlib.sha256
                    ).hexdigest(),
                )
            )
        assert_transport_signature(records_call)
        challenge_call = next(
            row for row in self.seen if row["path"].endswith("/challenge")
        )
        self.assertEqual(
            challenge_call["body"],
            {"bootstrapNonce": BOOTSTRAP_NONCE},
        )
        assert_transport_signature(challenge_call)
        second_records_call = next(
            row
            for row in self.seen
            if row["path"].endswith("/records")
            and row["body"] == {"cursor": "synthetic_cursor_marker"}
        )
        assert_transport_signature(second_records_call)
        participation_call = next(
            row
            for row in self.seen
            if row["path"].endswith("/participation")
            and row["body"] == {}
        )
        self.assertEqual(participation_call["cookie"], f"{COOKIE_NAME}={COOKIE_VALUE}")
        assert_transport_signature(participation_call)
        participation_next_call = next(
            row
            for row in self.seen
            if row["path"].endswith("/participation")
            and row["body"] == {"cursor": "a" * 64}
        )
        assert_transport_signature(participation_next_call)
        voice_transitions_call = next(
            row
            for row in self.seen
            if row["path"].endswith("/voice-state-transitions")
            and row["body"] == {}
        )
        self.assertEqual(
            voice_transitions_call["cookie"],
            f"{COOKIE_NAME}={COOKIE_VALUE}",
        )
        assert_transport_signature(voice_transitions_call)
        voice_transitions_next_call = next(
            row
            for row in self.seen
            if row["path"].endswith("/voice-state-transitions")
            and row["body"] == {"cursor": "b" * 64}
        )
        assert_transport_signature(voice_transitions_next_call)
        legal_call = next(
            row for row in self.seen if row["path"].endswith("/legal-minimal")
        )
        self.assertEqual(legal_call["cookie"], f"{COOKIE_NAME}={COOKIE_VALUE}")
        assert_transport_signature(legal_call)
        preview_call = next(
            row for row in self.seen if row["path"].endswith("delete/preview")
        )
        self.assertEqual(preview_call["body"], preview_body)
        assert_transport_signature(preview_call)
        self.assertEqual(
            next(row for row in self.seen if row["path"].endswith("delete/apply"))["body"],
            apply_body,
        )

    async def test_launcher_page_keeps_relative_ui_assets_available(self) -> None:
        page = await self.client.get("/archive/admin")
        asset = await self.client.get(
            "/archive/assets/evelyn-conversation-archive-admin.js"
        )
        stylesheet = await self.client.get(
            "/assets/evelyn-conversation-archive-admin.css"
        )

        self.assertEqual(page.status, 200)
        self.assertEqual(asset.status, 200)
        self.assertEqual(stylesheet.status, 200)
        self.assertIn("no-store", page.headers["Cache-Control"])
        self.assertIn("no-store", asset.headers["Cache-Control"])
        self.assertEqual(page.headers["X-Frame-Options"], "DENY")
        self.assertEqual(
            page.headers["Content-Security-Policy"],
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; form-action 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; object-src 'none'",
        )
        self.assertEqual(page.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(
            page.headers["Cross-Origin-Opener-Policy"], "same-origin"
        )
        body = await page.text()
        self.assertIn("evelyn-conversation-archive-admin.js", body)
        self.assertNotIn("pixi-8.13.1.min.js", body)
        self.assertNotIn("<script>", body)
        script = await asset.text()
        self.assertIn('deletionState === "local_cleanup_pending"', script)
        self.assertIn("완료로 표시하지 않습니다", script)

    async def test_feedback_admin_proxy_forwards_exact_bodies_and_projects_content_free_state(
        self,
    ) -> None:
        prefix = "/api/control-page/conversation-archive/admin/feedback"
        requests = (
            ("workflows", {}),
            (
                "capture",
                {
                    "taskId": "synthetic-task",
                    "sourceRecordId": "synthetic-record-1",
                    "category": "answer_quality",
                    "correction": "SYNTHETIC_PRIVATE_CORRECTION",
                    "nonce": "synthetic-nonce",
                },
            ),
            (
                "generalize",
                {
                    "workflowId": "synthetic-workflow",
                    "guidance": "SYNTHETIC_OPERATOR_GUIDANCE",
                    "privacyReview": {
                        "schema": "evelyn.feedback-privacy-review.v1"
                    },
                    "ancestorVersionIds": [],
                },
            ),
            (
                "evaluate",
                {
                    "versionId": "synthetic-version",
                    "evalRunId": "synthetic-eval",
                    "baselineContractDigest": "a" * 64,
                    "candidateContractDigest": "b" * 64,
                    "report": {"schema": "evelyn.task-agent-eval-report.v1"},
                },
            ),
            ("approval/preview", {"versionId": "synthetic-version"}),
            (
                "approval/apply",
                {"previewToken": "synthetic_feedback_preview", "code": "Aa1Z"},
            ),
            (
                "canary",
                {
                    "versionId": "synthetic-version",
                    "canaryRunId": "synthetic-canary",
                    "phase": "begin",
                },
            ),
            ("activate", {"versionId": "synthetic-version"}),
            (
                "failure",
                {
                    "versionId": "synthetic-version",
                    "failureId": "synthetic-failure",
                    "taskId": "synthetic-task",
                    "contractVersion": "evelyn.task-work-contract.v1",
                    "evaluatorVersion": "evelyn.task-agent-eval-suite.v1",
                    "failureCode": "grounding_regression",
                },
            ),
            (
                "rollback/preview",
                {
                    "versionId": "synthetic-version",
                    "contractVersion": "evelyn.task-work-contract.v1",
                    "evaluatorVersion": "evelyn.task-agent-eval-suite.v1",
                },
            ),
            (
                "rollback/apply",
                {"previewToken": "synthetic_feedback_preview", "code": "Z1aB"},
            ),
            (
                "revoke/preview",
                {
                    "versionId": "synthetic-version",
                    "reason": "source_dependency_detected",
                },
            ),
            (
                "revoke/apply",
                {"previewToken": "synthetic_feedback_preview", "code": "B2cD"},
            ),
        )

        responses: dict[str, dict[str, object]] = {}
        for action, body in requests:
            response = await self.post(
                f"{prefix}/{action}",
                body,
                cookie=True,
            )
            self.assertEqual(response.status, 200, action)
            responses[action] = await response.json()
            self.assertIn("no-store", response.headers["Cache-Control"])

        workflows = responses["workflows"]
        self.assertEqual(workflows["activeVersionId"], "base")
        workflow = workflows["workflows"][0]
        self.assertIs(workflow["contentFree"], True)
        self.assertNotIn("correction", workflow)
        self.assertNotIn("guidance", workflow)
        self.assertNotIn("principal", json.dumps(workflow))
        self.assertEqual(
            responses["approval/preview"]["guidance"],
            "SYNTHETIC_OPERATOR_GUIDANCE",
        )
        self.assertEqual(responses["rollback/preview"]["guidance"], "")
        self.assertEqual(responses["revoke/preview"]["guidance"], "")

        for action, body in requests:
            call = next(
                row
                for row in self.seen
                if row["path"].endswith(f"/feedback/{action}")
            )
            self.assertEqual(call["method"], "POST", action)
            self.assertEqual(call["body"], body, action)
            self.assertEqual(call["cookie"], f"{COOKIE_NAME}={COOKIE_VALUE}", action)
            self.assertNotIn("browser-secret", str(call["cookie"]))

    async def test_feedback_proxy_rejects_private_workflow_and_nonapproval_guidance(
        self,
    ) -> None:
        prefix = "/api/control-page/conversation-archive/admin/feedback"
        self.feedback_workflow_leaks_private_body = True
        workflows = await self.post(f"{prefix}/workflows", {}, cookie=True)
        self.assertEqual(workflows.status, 502)
        self.assertNotIn("SYNTHETIC_PRIVATE_CORRECTION", await workflows.text())

        self.feedback_workflow_leaks_private_body = False
        self.feedback_nonapproval_preview_leaks_guidance = True
        bad_preview_bodies = {
            "rollback/preview": {
                "versionId": "synthetic-version",
                "contractVersion": "evelyn.task-work-contract.v1",
                "evaluatorVersion": "evelyn.task-agent-eval-suite.v1",
            },
            "revoke/preview": {
                "versionId": "synthetic-version",
                "reason": "source_dependency_detected",
            },
        }
        for action, body in bad_preview_bodies.items():
            response = await self.post(
                f"{prefix}/{action}",
                body,
                cookie=True,
            )
            self.assertEqual(response.status, 502, action)
            self.assertNotIn("SYNTHETIC_FORBIDDEN_GUIDANCE", await response.text())

    async def test_admin_page_is_hidden_outside_dedicated_origin(self) -> None:
        with patch.object(
            control_page_server,
            "_conversation_archive_request_is_secure",
            return_value=False,
        ):
            page = await self.client.get("/archive/admin")

        self.assertEqual(page.status, 404)
        self.assertNotIn("evelyn-conversation-archive-admin.js", await page.text())

    async def test_dedicated_origin_allows_csrf_bootstrap_only(self) -> None:
        with patch.object(
            control_page_server,
            "_conversation_archive_request_uses_admin_origin",
            return_value=True,
        ):
            session = await self.client.get("/api/control-page/session")
            admin_page = await self.client.get("/archive/admin")
            ordinary_page = await self.client.get("/")
            ordinary_api = await self.client.get("/api/control-page/memory")

        self.assertEqual(session.status, 200)
        self.assertIn("csrfToken", await session.json())
        self.assertEqual(admin_page.status, 200)
        self.assertEqual(ordinary_page.status, 404)
        self.assertEqual(ordinary_api.status, 404)

    async def test_disabled_and_missing_session_are_content_free(self) -> None:
        prefix = "/api/control-page/conversation-archive/admin"
        with patch.object(
            control_page_server,
            "CONVERSATION_ARCHIVE_ENABLED",
            False,
        ):
            disabled = await self.post(
                f"{prefix}/challenge",
                {"bootstrapNonce": BOOTSTRAP_NONCE},
            )
        self.assertEqual(disabled.status, 503)
        self.assertEqual(
            await disabled.json(),
            {"ok": False, "error": "conversation_archive_unavailable"},
        )
        self.assertIn("no-store", disabled.headers["Cache-Control"])
        self.assertFalse(self.seen)

        invalid_query = await self.client.get(
            f"{prefix}/records?unexpected=synthetic",
            headers=self.browser_headers(cookie=True),
        )
        self.assertEqual(invalid_query.status, 400)
        self.assertEqual(
            (await invalid_query.json())["error"],
            "conversation_archive_request_invalid",
        )
        self.assertFalse(self.seen)

        invalid_cursor = await self.client.get(
            f"{prefix}/records?cursor=synthetic%2Fcursor",
            headers=self.browser_headers(cookie=True),
        )
        self.assertEqual(invalid_cursor.status, 400)
        self.assertFalse(self.seen)

        oversized_metadata_cursor = await self.client.get(
            f"{prefix}/participation?cursor={'a' * 65}",
            headers=self.browser_headers(cookie=True),
        )
        self.assertEqual(oversized_metadata_cursor.status, 400)
        self.assertFalse(self.seen)

        oversized_voice_transition_cursor = await self.client.get(
            f"{prefix}/voice-state-transitions?cursor={'b' * 65}",
            headers=self.browser_headers(cookie=True),
        )
        self.assertEqual(oversized_voice_transition_cursor.status, 400)
        self.assertFalse(self.seen)

        missing_cookie = await self.client.get(
            f"{prefix}/records",
            headers=self.browser_headers(),
        )
        self.assertEqual(missing_cookie.status, 401)
        self.assertEqual(
            await missing_cookie.json(),
            {
                "ok": False,
                "error": "conversation_archive_admin_authentication_required",
            },
        )
        self.assertFalse(self.seen)

        self.records_authentication_required = True
        expired_session = await self.client.get(
            f"{prefix}/records",
            headers=self.browser_headers(cookie=True),
        )
        self.assertEqual(expired_session.status, 403)
        self.assertEqual(
            (await expired_session.json())["state"],
            "authentication_required",
        )
        expired_cookies = expired_session.headers.getall("Set-Cookie", [])
        self.assertEqual(len(expired_cookies), 1)
        self.assertIn(f"{COOKIE_NAME}=", expired_cookies[0])
        self.assertIn("Max-Age=0", expired_cookies[0])

    async def test_security_boundary_rejects_csrf_origin_and_http(self) -> None:
        prefix = "/api/control-page/conversation-archive/admin"
        missing_csrf = await self.client.post(
            f"{prefix}/challenge",
            json={"bootstrapNonce": BOOTSTRAP_NONCE},
            headers=self.browser_headers(),
        )
        self.assertEqual(missing_csrf.status, 403)
        self.assertIn("no-store", missing_csrf.headers["Cache-Control"])

        bad_origin = await self.client.post(
            f"{prefix}/challenge",
            json={"bootstrapNonce": BOOTSTRAP_NONCE},
            headers=self.browser_headers(
                csrf=True,
                origin="https://synthetic.invalid",
            ),
        )
        self.assertEqual(bad_origin.status, 403)
        self.assertIn("no-store", bad_origin.headers["Cache-Control"])

        with patch.object(
            control_page_server,
            "_conversation_archive_request_is_secure",
            return_value=False,
        ):
            insecure = await self.post(
                f"{prefix}/challenge",
                {"bootstrapNonce": BOOTSTRAP_NONCE},
            )
        self.assertEqual(insecure.status, 403)
        self.assertEqual(
            await insecure.json(),
            {
                "ok": False,
                "error": "conversation_archive_local_https_required",
            },
        )
        self.assertFalse(self.seen)

    async def test_upstream_auth_secret_or_weak_cookie_fails_closed(self) -> None:
        prefix = "/api/control-page/conversation-archive/admin"
        self.challenge_leaks_code = True
        leaked = await self.post(
            f"{prefix}/challenge",
            {"bootstrapNonce": BOOTSTRAP_NONCE},
        )
        self.assertEqual(leaked.status, 502)
        leaked_text = await leaked.text()
        self.assertNotIn("Aa1Z", leaked_text)
        self.assertEqual(
            json.loads(leaked_text)["error"],
            "conversation_archive_proxy_invalid",
        )

        self.challenge_leaks_code = False
        self.challenge_extra_field = True
        unexpected = await self.post(
            f"{prefix}/challenge",
            {"bootstrapNonce": BOOTSTRAP_NONCE},
        )
        self.assertEqual(unexpected.status, 502)
        self.assertNotIn(
            "SYNTHETIC_UNEXPECTED_SUCCESS_MARKER",
            await unexpected.text(),
        )

        self.challenge_extra_field = False
        self.challenge_sets_cookie = True
        injected_cookie = await self.post(
            f"{prefix}/challenge",
            {"bootstrapNonce": BOOTSTRAP_NONCE},
        )
        self.assertEqual(injected_cookie.status, 502)
        self.assertFalse(injected_cookie.headers.getall("Set-Cookie", []))

        self.challenge_sets_cookie = False
        self.challenge_redirects = True
        seen_before_redirect = len(self.seen)
        redirected = await self.post(
            f"{prefix}/challenge",
            {"bootstrapNonce": BOOTSTRAP_NONCE},
            cookie=True,
        )
        self.assertEqual(redirected.status, 502)
        self.assertEqual(len(self.seen), seen_before_redirect + 1)
        self.assertTrue(self.seen[-1]["path"].endswith("/challenge"))
        self.assertEqual(self.seen[-1]["cookie"], "")

        self.challenge_redirects = False
        self.login_cookie_is_invalid = True
        invalid_cookie = await self.post(
            f"{prefix}/login",
            {
                "challengeId": "synthetic_challenge_marker_1234",
                "code": "Aa1Z",
            },
        )
        self.assertEqual(invalid_cookie.status, 502)
        self.assertFalse(invalid_cookie.headers.getall("Set-Cookie", []))

        self.login_cookie_is_invalid = False
        self.login_cookie_is_immediately_deleted = True
        deleted_login_cookie = await self.post(
            f"{prefix}/login",
            {
                "challengeId": "synthetic_challenge_marker_1234",
                "code": "Aa1Z",
            },
        )
        self.assertEqual(deleted_login_cookie.status, 502)
        self.assertFalse(deleted_login_cookie.headers.getall("Set-Cookie", []))

    async def test_upstream_error_projects_only_public_fields(self) -> None:
        prefix = "/api/control-page/conversation-archive/admin"
        self.challenge_private_error = True

        response = await self.post(
            f"{prefix}/challenge",
            {"bootstrapNonce": BOOTSTRAP_NONCE},
        )

        self.assertEqual(response.status, 403)
        self.assertEqual(
            await response.json(),
            {
                "ok": False,
                "error": "archive_transport_auth_invalid",
                "state": "rejected",
                "retryable": False,
            },
        )
        self.assertNotIn("SYNTHETIC_PRIVATE_ERROR_MARKER", await response.text())
        self.assertIn("no-store", response.headers["Cache-Control"])

    async def test_missing_proxy_key_fails_closed_before_upstream(self) -> None:
        prefix = "/api/control-page/conversation-archive/admin"
        missing = Path(self.temporary.name) / "missing-proxy.key"
        with patch.object(
            control_page_server,
            "CONVERSATION_ARCHIVE_PROXY_KEY_FILE",
            str(missing),
        ):
            response = await self.post(
                f"{prefix}/challenge",
                {"bootstrapNonce": BOOTSTRAP_NONCE},
            )

        self.assertEqual(response.status, 503)
        self.assertEqual(
            await response.json(),
            {
                "ok": False,
                "error": "conversation_archive_authorization_unavailable",
            },
        )
        self.assertFalse(self.seen)

    async def test_nonfinite_request_fails_before_upstream(self) -> None:
        prefix = "/api/control-page/conversation-archive/admin"
        response = await self.post(
            f"{prefix}/delete/preview",
            {
                "targetPrincipalId": "synthetic-principal",
                "startedAt": float("nan"),
                "endedAt": "2030-01-02T00:00:00Z",
            },
            cookie=True,
        )

        self.assertEqual(response.status, 400)
        self.assertEqual(
            (await response.json())["error"],
            "conversation_archive_request_invalid",
        )
        self.assertFalse(self.seen)

    async def test_records_page_over_fixed_limit_fails_closed(self) -> None:
        prefix = "/api/control-page/conversation-archive/admin"
        self.records_over_page_limit = True

        response = await self.client.get(
            f"{prefix}/records",
            headers=self.browser_headers(cookie=True),
        )

        self.assertEqual(response.status, 502)
        self.assertEqual(
            (await response.json())["error"],
            "conversation_archive_proxy_invalid",
        )
        self.assertNotIn(SYNTHETIC_BODY, await response.text())

    async def test_legal_minimal_proxy_rejects_identifier_leak(self) -> None:
        self.legal_leaks_identifier = True
        response = await self.client.get(
            "/api/control-page/conversation-archive/admin/legal-minimal",
            headers=self.browser_headers(cookie=True),
        )

        self.assertEqual(response.status, 502)
        self.assertEqual(
            (await response.json())["error"],
            "conversation_archive_proxy_invalid",
        )
        self.assertNotIn("must-not-cross", await response.text())

    async def test_voice_state_transition_proxy_rejects_body_leak(self) -> None:
        self.voice_transition_leaks_body = True
        response = await self.client.get(
            "/api/control-page/conversation-archive/admin/voice-state-transitions",
            headers=self.browser_headers(cookie=True),
        )

        self.assertEqual(response.status, 502)
        self.assertEqual(
            (await response.json())["error"],
            "conversation_archive_proxy_invalid",
        )
        self.assertNotIn("must-not-cross", await response.text())


class ControlPageConversationArchiveUiContractTests(unittest.TestCase):
    def test_dedicated_admin_origin_exposes_only_admin_surface(self) -> None:
        for path in (
            "/archive/admin",
            "/api/control-page/session",
            "/assets/evelyn-conversation-archive-admin.css",
            "/assets/evelyn-conversation-archive-admin.js",
            "/api/control-page/conversation-archive/admin/records",
            "/api/control-page/conversation-archive/admin/participation",
            "/api/control-page/conversation-archive/admin/voice-state-transitions",
            "/api/control-page/conversation-archive/admin/legal-minimal",
            "/api/control-page/conversation-archive/admin/feedback/workflows",
            "/api/control-page/conversation-archive/admin/feedback/revoke/apply",
        ):
            with self.subTest(path=path):
                self.assertTrue(
                    control_page_server._conversation_archive_admin_origin_path_allowed(
                        path
                    )
                )
        for path in (
            "/",
            "/health",
            "/api/control-page/memory",
            "/assets/evelyn-control-page.js",
            "/archive/assets/evelyn-conversation-archive-admin.js",
        ):
            with self.subTest(path=path):
                self.assertFalse(
                    control_page_server._conversation_archive_admin_origin_path_allowed(
                        path
                    )
                )

    def test_archive_https_boundary_requires_loopback_browser_host(self) -> None:
        loopback_request = type(
            "Request",
            (),
            {
                "method": "POST",
                "scheme": "https",
                "headers": {
                    "Host": "127.0.0.1:8800",
                    "Origin": "https://127.0.0.1:8800",
                },
            },
        )()
        remote_host_request = type(
            "Request",
            (),
            {
                "method": "POST",
                "scheme": "https",
                "headers": {
                    "Host": "synthetic.invalid:8799",
                    "Origin": "https://synthetic.invalid:8799",
                },
            },
        )()

        self.assertTrue(
            control_page_server._conversation_archive_request_is_secure(
                loopback_request
            )
        )
        self.assertFalse(
            control_page_server._conversation_archive_request_is_secure(
                remote_host_request
            )
        )
        missing_origin_request = type(
            "Request",
            (),
            {
                "method": "POST",
                "scheme": "https",
                "headers": {"Host": "127.0.0.1:8799"},
            },
        )()
        self.assertFalse(
            control_page_server._conversation_archive_request_is_secure(
                missing_origin_request
            )
        )
        ordinary_control_page_request = type(
            "Request",
            (),
            {
                "method": "POST",
                "scheme": "https",
                "headers": {
                    "Host": "127.0.0.1:8799",
                    "Origin": "https://127.0.0.1:8799",
                },
            },
        )()
        self.assertFalse(
            control_page_server._conversation_archive_request_is_secure(
                ordinary_control_page_request
            )
        )

    def test_ui_keeps_private_state_in_memory_and_uses_exact_endpoints(self) -> None:
        index = (REPO_ROOT / "docs" / "index.html").read_text(encoding="utf-8")
        admin = (REPO_ROOT / "docs" / "archive-admin.html").read_text(
            encoding="utf-8"
        )
        script = (
            REPO_ROOT
            / "docs"
            / "assets"
            / "evelyn-conversation-archive-admin.js"
        ).read_text(encoding="utf-8")

        self.assertNotIn('id="conversationArchiveAdminPanel"', index)
        self.assertNotIn("evelyn-conversation-archive-admin.js", index)
        self.assertIn('id="conversationArchiveAdminPanel"', admin)
        self.assertIn("hidden", admin)
        self.assertIn(
            'id="conversationArchiveChallengeButton" type="button" disabled',
            admin,
        )
        self.assertIn("evelyn-conversation-archive-admin.js", admin)
        for suffix in (
            "/challenge",
            "/login",
            "/records",
            "/delete/preview",
            "/delete/apply",
            "/feedback/workflows",
            "/feedback/capture",
            "/feedback/generalize",
            "/feedback/evaluate",
            "/feedback/approval/preview",
            "/feedback/approval/apply",
            "/feedback/canary",
            "/feedback/activate",
            "/feedback/failure",
            "/feedback/rollback/preview",
            "/feedback/rollback/apply",
            "/feedback/revoke/preview",
            "/feedback/revoke/apply",
            "/logout",
        ):
            self.assertIn(suffix, script)
        self.assertIn('credentials: "same-origin"', script)
        self.assertIn('cache: "no-store"', script)
        self.assertIn('location.pathname !== "/archive/admin"', script)
        self.assertIn("#archive-bootstrap=", script)
        self.assertIn("history.replaceState", script)
        self.assertIn("challengeBody = { bootstrapNonce }", script)
        self.assertIn("body: challengeBody", script)
        self.assertIn('"authentication_required"', script)
        self.assertIn("textContent", script)
        self.assertIn("nextCursor", script)
        self.assertIn('id="conversationArchiveParticipation"', admin)
        self.assertIn('id="conversationArchiveVoiceTransitions"', admin)
        self.assertIn('id="conversationArchiveLegalEvents"', admin)
        self.assertIn("renderParticipation", script)
        self.assertIn("renderVoiceStateTransitions", script)
        self.assertIn("renderLegalEvents", script)
        self.assertIn('participation: {', script)
        self.assertIn('"voice-state-transitions": {', script)
        self.assertIn('"legal-minimal": {', script)
        self.assertIn('archiveRequest("/" + kind + query)', script)
        self.assertIn("[event.ownerName, event.occurredAt]", script)
        self.assertIn('id="conversationArchiveFeedbackRevokePreviewForm"', admin)
        self.assertIn('id="conversationArchiveFeedbackRevokeApplyForm"', admin)
        self.assertIn("feedbackRevokePreviewToken", script)
        canary_block = script[
            script.index('feedbackCanaryForm.addEventListener("submit"') :
            script.index('feedbackActivateForm.addEventListener("submit"')
        ]
        self.assertIn('phase: "begin"', canary_block)
        self.assertNotIn("aggregate", canary_block)
        self.assertNotIn('"complete"', canary_block)
        self.assertNotIn('name="aggregate"', admin)
        self.assertNotIn('name="phase"', admin)
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("localStorage", script)
        self.assertNotIn("sessionStorage", script)
        self.assertNotIn("console.", script)


if __name__ == "__main__":
    unittest.main()
