from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp import web


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_http import (  # noqa: E402
    CONTROL_PAGE_CSRF_HEADER,
    CONTROL_PAGE_CSRF_TOKEN,
    CONTROL_PAGE_NO_STORE_HEADERS,
    add_control_page_cors_headers,
    add_control_page_no_store_headers,
    build_control_page_health_payload,
    control_page_api_cors_applies,
    control_page_origin_is_allowed,
    control_page_cors_middleware,
    control_page_session_handler,
    control_page_file_response,
    control_page_json_response,
    resolve_control_page_asset_path,
    reject_browser_origin_middleware,
    request_control_page_host_is_allowed,
)
from evelyn_core.memory_deletion_journal import (  # noqa: E402
    MemoryDeletionJournalBusyError,
    MemoryDeletionJournalIntegrityError,
)


class ControlPageHttpTests(unittest.TestCase):
    def test_json_response_preserves_utf8_payload_contract(self) -> None:
        response = control_page_json_response({"ok": True, "message": "안녕"}, status=202)

        self.assertEqual(response.status, 202)
        self.assertEqual(response.content_type, "application/json")
        self.assertEqual(json.loads(response.text), {"ok": True, "message": "안녕"})
        self.assertIn("안녕", response.text)

    def test_no_store_headers_are_shared_by_file_and_binary_responses(self) -> None:
        response = add_control_page_no_store_headers(web.Response(body=b"x"))

        for key, value in CONTROL_PAGE_NO_STORE_HEADERS.items():
            self.assertEqual(response.headers[key], value)

    def test_file_response_rejects_missing_paths_and_sets_no_store_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "index.html"
            path.write_text("ok", encoding="utf-8")

            response = control_page_file_response(path, not_found_text="missing")

            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Cache-Control"], CONTROL_PAGE_NO_STORE_HEADERS["Cache-Control"])
            with self.assertRaises(web.HTTPNotFound):
                control_page_file_response(path.with_name("missing.html"), not_found_text="missing")

    def test_asset_path_resolution_stays_inside_assets_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset = root / "assets" / "app.js"
            asset.parent.mkdir()
            asset.write_text("console.log('ok')", encoding="utf-8")

            self.assertEqual(resolve_control_page_asset_path(asset.parent, "app.js"), asset.resolve())
            with self.assertRaises(web.HTTPForbidden):
                resolve_control_page_asset_path(asset.parent, "../outside.js")
            with self.assertRaises(web.HTTPNotFound):
                resolve_control_page_asset_path(asset.parent, "missing.js")

    def test_cors_helpers_apply_only_to_control_page_api_paths(self) -> None:
        self.assertTrue(control_page_api_cors_applies("/api/control-page/state"))
        self.assertFalse(control_page_api_cors_applies("/health"))

        api_response = add_control_page_cors_headers(
            web.Response(status=200),
            path="/api/control-page/state",
            origin="http://127.0.0.1:8799",
        )
        health_response = add_control_page_cors_headers(web.Response(status=200), path="/health")

        self.assertEqual(api_response.headers["Access-Control-Allow-Origin"], "http://127.0.0.1:8799")
        self.assertNotEqual(api_response.headers["Access-Control-Allow-Origin"], "*")
        self.assertIn(CONTROL_PAGE_CSRF_HEADER, api_response.headers["Access-Control-Allow-Headers"])
        self.assertNotIn("Access-Control-Allow-Origin", health_response.headers)

    def test_cors_middleware_short_circuits_options_for_control_page_api(self) -> None:
        class Request:
            method = "OPTIONS"
            path = "/api/control-page/state"
            headers = {"Host": "127.0.0.1:8799", "Origin": "http://127.0.0.1:8799"}
            scheme = "http"

        async def handler(_request):
            return web.Response(status=500)

        response = asyncio.run(control_page_cors_middleware(Request(), handler))

        self.assertEqual(response.status, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Methods"], "GET,POST,OPTIONS")

    def test_cors_middleware_rejects_untrusted_browser_origin(self) -> None:
        class Request:
            method = "GET"
            path = "/api/control-page/state"
            headers = {"Host": "127.0.0.1:8799", "Origin": "https://evil.example"}
            scheme = "http"

        called = False

        async def handler(_request):
            nonlocal called
            called = True
            return web.Response(status=200)

        response = asyncio.run(control_page_cors_middleware(Request(), handler))

        self.assertEqual(response.status, 403)
        self.assertFalse(called)
        self.assertEqual(json.loads(response.text)["error"], "origin_not_allowed")
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_mutating_control_request_requires_csrf_token(self) -> None:
        class MissingTokenRequest:
            method = "POST"
            path = "/api/control-page/shutdown"
            headers = {"Host": "127.0.0.1:8799", "Origin": "http://127.0.0.1:8799"}
            scheme = "http"

        class ValidTokenRequest:
            method = "POST"
            path = "/api/control-page/shutdown"
            headers = {
                "Host": "127.0.0.1:8799",
                "Origin": "http://127.0.0.1:8799",
                "Content-Type": "application/json; charset=utf-8",
                CONTROL_PAGE_CSRF_HEADER: CONTROL_PAGE_CSRF_TOKEN,
            }
            scheme = "http"

        class NonJsonRequest:
            method = "POST"
            path = "/api/control-page/shutdown"
            headers = {
                "Host": "127.0.0.1:8799",
                "Origin": "http://127.0.0.1:8799",
                CONTROL_PAGE_CSRF_HEADER: CONTROL_PAGE_CSRF_TOKEN,
            }
            scheme = "http"

        calls = 0

        async def handler(_request):
            nonlocal calls
            calls += 1
            return web.Response(status=202)

        rejected = asyncio.run(control_page_cors_middleware(MissingTokenRequest(), handler))
        non_json = asyncio.run(control_page_cors_middleware(NonJsonRequest(), handler))
        accepted = asyncio.run(control_page_cors_middleware(ValidTokenRequest(), handler))

        self.assertEqual(rejected.status, 403)
        self.assertEqual(json.loads(rejected.text)["error"], "csrf_token_required")
        self.assertEqual(non_json.status, 415)
        self.assertEqual(json.loads(non_json.text)["error"], "json_content_type_required")
        self.assertEqual(accepted.status, 202)
        self.assertEqual(calls, 1)

    def test_control_page_integrity_failure_is_content_free_503(self) -> None:
        private_detail = (
            r"C:\private\memory_deletions.jsonl secret-note-body"
        )

        class Request:
            method = "GET"
            path = "/api/control-page/memory/private-note-id"
            headers = {
                "Host": "127.0.0.1:8799",
                "Origin": "http://127.0.0.1:8799",
            }
            scheme = "http"

        async def handler(_request):
            raise MemoryDeletionJournalIntegrityError(private_detail)

        response = asyncio.run(
            control_page_cors_middleware(Request(), handler)
        )

        self.assertEqual(response.status, 503)
        self.assertEqual(
            json.loads(response.text),
            {
                "ok": False,
                "error": "memory_deletion_journal_integrity_failed",
            },
        )
        self.assertNotIn(private_detail, response.text)
        self.assertEqual(
            response.headers["Cache-Control"],
            CONTROL_PAGE_NO_STORE_HEADERS["Cache-Control"],
        )
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"],
            "http://127.0.0.1:8799",
        )

    def test_internal_api_integrity_failure_is_content_free_503(self) -> None:
        private_detail = "private transcript and note path"
        request = type(
            "Request",
            (),
            {
                "method": "GET",
                "path": "/api/control-page/memory",
                "headers": {},
            },
        )()

        async def handler(_request):
            raise MemoryDeletionJournalIntegrityError(private_detail)

        response = asyncio.run(
            reject_browser_origin_middleware(request, handler)
        )

        self.assertEqual(response.status, 503)
        self.assertEqual(
            json.loads(response.text),
            {
                "ok": False,
                "error": "memory_deletion_journal_integrity_failed",
            },
        )
        self.assertNotIn(private_detail, response.text)

    def test_busy_failure_is_exact_retryable_content_free_503(self) -> None:
        private_detail = "private transcript and lock path"

        class Request:
            method = "GET"
            path = "/api/control-page/memory"
            headers = {
                "Host": "127.0.0.1:8799",
                "Origin": "http://127.0.0.1:8799",
            }
            scheme = "http"

        async def handler(_request):
            raise MemoryDeletionJournalBusyError(private_detail)

        response = asyncio.run(
            control_page_cors_middleware(Request(), handler)
        )

        self.assertEqual(response.status, 503)
        self.assertEqual(
            json.loads(response.text),
            {"ok": False, "error": "memory_deletion_journal_busy"},
        )
        self.assertNotIn(private_detail, response.text)
        self.assertEqual(
            response.headers["Cache-Control"],
            CONTROL_PAGE_NO_STORE_HEADERS["Cache-Control"],
        )

        class InternalRequest:
            method = "GET"
            path = "/api/control-page/memory"
            headers = {}

        async def result_handler(_request):
            return control_page_json_response(
                {
                    "ok": False,
                    "error": "memory_deletion_journal_busy",
                    "detail": private_detail,
                },
                status=418,
            )

        internal = asyncio.run(
            reject_browser_origin_middleware(
                InternalRequest(),
                result_handler,
            )
        )
        self.assertEqual(internal.status, 503)
        self.assertEqual(
            json.loads(internal.text),
            {"ok": False, "error": "memory_deletion_journal_busy"},
        )
        self.assertNotIn(private_detail, internal.text)

    def test_result_shaped_integrity_failure_is_collapsed_at_cors_boundary(
        self,
    ) -> None:
        private_detail = "private note body and C:/secret/path"

        class Request:
            method = "GET"
            path = "/api/control-page/memory/private-note-id"
            headers = {
                "Host": "127.0.0.1:8799",
                "Origin": "http://127.0.0.1:8799",
            }
            scheme = "http"

        async def handler(_request):
            return control_page_json_response(
                {
                    "ok": False,
                    "error": "memory_deletion_journal_integrity_failed",
                    "detail": private_detail,
                    "body": "private transcript",
                },
                status=418,
            )

        response = asyncio.run(
            control_page_cors_middleware(Request(), handler)
        )

        self.assertEqual(response.status, 503)
        self.assertEqual(
            json.loads(response.text),
            {
                "ok": False,
                "error": "memory_deletion_journal_integrity_failed",
            },
        )
        self.assertNotIn(private_detail, response.text)
        self.assertEqual(
            response.headers["Cache-Control"],
            CONTROL_PAGE_NO_STORE_HEADERS["Cache-Control"],
        )
        self.assertEqual(
            response.headers["Access-Control-Allow-Origin"],
            "http://127.0.0.1:8799",
        )

    def test_result_shaped_integrity_failure_is_collapsed_at_internal_boundary(
        self,
    ) -> None:
        private_detail = "private note body and C:/secret/path"
        request = type(
            "Request",
            (),
            {
                "method": "GET",
                "path": "/api/control-page/memory/private-note-id",
                "headers": {},
            },
        )()

        async def handler(_request):
            return control_page_json_response(
                {
                    "ok": False,
                    "error": "memory_deletion_journal_integrity_failed",
                    "path": private_detail,
                },
                status=200,
            )

        response = asyncio.run(
            reject_browser_origin_middleware(request, handler)
        )

        self.assertEqual(response.status, 503)
        self.assertEqual(
            json.loads(response.text),
            {
                "ok": False,
                "error": "memory_deletion_journal_integrity_failed",
            },
        )
        self.assertNotIn(private_detail, response.text)
        self.assertEqual(
            response.headers["Cache-Control"],
            CONTROL_PAGE_NO_STORE_HEADERS["Cache-Control"],
        )

    def test_session_endpoint_returns_no_store_csrf_contract(self) -> None:
        response = asyncio.run(control_page_session_handler(None))
        payload = json.loads(response.text)

        self.assertEqual(payload["csrfToken"], CONTROL_PAGE_CSRF_TOKEN)
        self.assertEqual(payload["csrfHeader"], CONTROL_PAGE_CSRF_HEADER)
        self.assertEqual(response.headers["Cache-Control"], CONTROL_PAGE_NO_STORE_HEADERS["Cache-Control"])

    def test_origin_validation_accepts_same_origin_and_rejects_null(self) -> None:
        same_origin = type(
            "Request",
            (),
            {
                "headers": {"Host": "localhost:8799", "Origin": "http://localhost:8799"},
                "scheme": "http",
            },
        )()
        null_origin = type(
            "Request",
            (),
            {"headers": {"Host": "localhost:8799", "Origin": "null"}, "scheme": "http"},
        )()

        self.assertTrue(control_page_origin_is_allowed(same_origin))
        self.assertFalse(control_page_origin_is_allowed(null_origin))

    def test_dns_rebinding_host_is_rejected_even_when_origin_matches_host(self) -> None:
        rebound_request = type(
            "Request",
            (),
            {
                "method": "GET",
                "path": "/api/control-page/state",
                "headers": {"Host": "evil.example:8799", "Origin": "http://evil.example:8799"},
                "scheme": "http",
            },
        )()

        self.assertFalse(request_control_page_host_is_allowed(rebound_request))
        self.assertFalse(control_page_origin_is_allowed(rebound_request))

        async def handler(_request):
            return web.Response(status=200)

        response = asyncio.run(control_page_cors_middleware(rebound_request, handler))
        self.assertEqual(response.status, 403)
        self.assertEqual(json.loads(response.text)["error"], "host_not_allowed")

    def test_internal_api_rejects_browser_origin_but_allows_server_call(self) -> None:
        browser_request = type(
            "Request",
            (),
            {
                "method": "POST",
                "path": "/api/control-page/shutdown",
                "headers": {"Origin": "http://127.0.0.1:8799", "Content-Type": "application/json"},
            },
        )()
        server_request = type(
            "Request",
            (),
            {"method": "POST", "path": "/api/control-page/chat", "headers": {"Content-Type": "application/json"}},
        )()

        async def handler(_request):
            return web.Response(status=204)

        rejected = asyncio.run(reject_browser_origin_middleware(browser_request, handler))
        accepted = asyncio.run(reject_browser_origin_middleware(server_request, handler))

        self.assertEqual(rejected.status, 403)
        self.assertEqual(json.loads(rejected.text)["error"], "browser_origin_not_allowed")
        self.assertEqual(accepted.status, 204)

    def test_internal_api_rejects_non_json_mutation(self) -> None:
        request = type(
            "Request",
            (),
            {"method": "POST", "path": "/api/control-page/shutdown", "headers": {}},
        )()

        async def handler(_request):
            return web.Response(status=204)

        response = asyncio.run(reject_browser_origin_middleware(request, handler))
        self.assertEqual(response.status, 415)
        self.assertEqual(json.loads(response.text)["error"], "json_content_type_required")

    def test_health_payload_matches_bot_api_contract(self) -> None:
        self.assertEqual(
            build_control_page_health_payload(local_only_mode=True, discord_enabled=False, port=8799),
            {
                "ok": True,
                "role": "bot-api",
                "controlPage": True,
                "localOnly": True,
                "discordEnabled": False,
                "port": 8799,
            },
        )


if __name__ == "__main__":
    unittest.main()
